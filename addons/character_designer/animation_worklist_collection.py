"""Explicit serial collection operations; drawing never scans or starts work.

Only the owning window/scene may advance a batch. A native export must publish
and dispose before another Action is activated. Receipts describe the frozen
successful publication, never whatever the artist edited while it ran.
"""

import json
import hashlib
from pathlib import Path
import uuid

import bpy

from . import animation_link as links, animation_worklist as worklist
from . import animation_workspace as workspace
from .animation_worklist_fingerprint import SCHEMA as FINGERPRINT_SCHEMA, fingerprint_native
from .animation_worklist_queue import ADD, SYNC, QueueItem, SerialQueue


RECEIPT_SCHEMA = 'character-designer.animation-worklist-receipt/1'
SOURCE_BASELINE_SCHEMA = 'character-designer.animation-source-baseline/1'
COMPOSITION_FINGERPRINT_SCHEMA = 'cdesigner.composition-b-content/1'
RIGGING_INPUT_SCHEMA = 'cdesigner.rigging-input-proof/1'
RIGGING_CAPTURE_KEY = 'character_designer_rigging_capture_v1'
RIGGING_INPUT_REF = 'character_designer_rigging_input_action_v1'
RIGGING_INPUT_FIELDS = frozenset({'schema', 'source_packet_sha256', 'work_item_id',
    'new_action_id', 'capture_sha256', 'base_action', 'base_slot', 'input_action',
    'input_slot', 'source_read_only', 'initial_input_motion', 'input_motion_sha256'})
_batch = None


def export_implementation():
    """Explicit scan/launch IO only; receipts also bind the export semantics."""
    root = Path(__file__).resolve().parent
    result = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        'animation_export.py', 'animation_export_worker.py', 'unity_export_worker.py',
        'animation_worklist_fingerprint.py')}
    # Approved releases before Dress export integration have neither optional
    # guard. Bind their absence explicitly; adding/removing either invalidates
    # receipts, while a partially installed pair is never a valid implementation.
    guards = ('dress_export_snapshot.py', 'dress_export_guard.py')
    present = [(root / name).is_file() for name in guards]
    if any(present) and not all(present):
        raise ValueError('The optional Dress export guard pair is incomplete.')
    result.update({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   if exists else 'absent' for name, exists in zip(guards, present)})
    composition = ('animation_composition_contract.py', 'animation_composition_controls.py',
                   'animation_composition_export.py', 'animation_composition_export_worker.py',
                   'animation_composition.py')
    present = [(root / name).is_file() for name in composition]
    if any(present) and not all(present):
        raise ValueError('The composition export implementation is incomplete.')
    result.update({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   if exists else 'absent' for name, exists in zip(composition, present)})
    if all(present):
        result['animation_worklist_collection.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result['animation_worklist_new.py'] = hashlib.sha256((root / 'animation_worklist_new.py').read_bytes()).hexdigest()
    rigging = ('unity_animation_rigging.py', 'animation_rigging_contract.py')
    present = [(root / name).is_file() for name in rigging]
    if any(present) and not all(present):
        raise ValueError('The Rigging input/export implementation pair is incomplete.')
    result.update({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   if exists else 'absent' for name, exists in zip(rigging, present)})
    if all(present):
        result['animation_worklist_collection.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


def _composition_row(item):
    # An explicit unsupported row cannot fall through to a legacy native proof.
    raw = getattr(item, 'composition_json', '')
    link = json.loads(item.link_identity) if getattr(item, 'link_identity', '') else {}
    if not isinstance(link, dict):
        raise ValueError('The animation row has invalid captured Link metadata.')
    scope = getattr(item, 'edit_scope', '') or link.get('editScope') or ''
    if scope not in {'', 'All', 'FingersLeft', 'FingersRight', 'FingersBoth', 'UpperBodyOverride'}:
        raise ValueError('The animation row has an unsupported editing scope.')
    return bool(raw or link.get('composition') is not None or
                getattr(item, 'application_role', '') or scope == 'UpperBodyOverride')


def _composition_fingerprint(context, item, *, source=False):
    from . import animation_composition as composition
    proof = composition.item_fingerprint(context, item, source=source)
    if proof is None:
        raise ValueError('The explicit composition has no authenticated B content proof.')
    proof = {**proof, 'composition_metadata': composition.composition_metadata(context, item)}
    if not _valid_proof(proof):
        raise ValueError('The composition B proof or reference metadata is incomplete.')
    return proof


def _rigging_row(item):
    action = getattr(item, 'custom_action', None)
    absent = object()
    return action is not None and (action.get(RIGGING_CAPTURE_KEY, absent) is not absent
                                   or action.get(RIGGING_INPUT_REF, absent) is not absent)


def _rigging_fingerprint(base, component):
    payload = json.dumps({'base_fingerprint': base, 'rigging': component},
        sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _valid_rigging_input(component):
    if (not isinstance(component, dict) or set(component) != RIGGING_INPUT_FIELDS
            or component.get('schema') != RIGGING_INPUT_SCHEMA
            or type(component['source_read_only']) is not bool
            or any(not _sha256_value(component[key]) for key in
                   ('source_packet_sha256', 'capture_sha256', 'initial_input_motion', 'input_motion_sha256'))
            or any(type(component[key]) is not int or not 0 <= component[key] < 2**31
                   for key in ('base_slot', 'input_slot'))
            or any(not isinstance(component[key], str) or not component[key].strip()
                   or component[key] != component[key].strip() for key in ('base_action', 'input_action'))
            or component['base_action'] == component['input_action']):
        return False
    try:
        for key in ('work_item_id', 'new_action_id'):
            value = component[key]
            if not isinstance(value, str) or uuid.UUID(value).hex != value or not uuid.UUID(value).int:
                return False
        return component['work_item_id'] != component['new_action_id']
    except (ValueError, TypeError, AttributeError):
        return False


def _with_rigging(proof, item, *, source=False):
    if not _rigging_row(item):
        return proof
    from . import unity_animation_rigging as rigging
    component = rigging.input_proof(item.rig, item.custom_action, item.custom_slot,
                                   item.work_item_id, source=source)
    if not _valid_rigging_input(component):
        raise ValueError('The fixed Target/Hint input proof is incomplete.')
    base = proof.get('fingerprint', '')
    return {**proof, 'base_fingerprint': base, 'rigging': component,
            'fingerprint': _rigging_fingerprint(base, component) if proof['fingerprint_known'] else ''}


def item_fingerprint(context, item):
    if _composition_row(item):
        return _composition_fingerprint(context, item)
    proof = fingerprint_native(item.custom_action, item.rig, context.scene, item.custom_slot)
    from . import hair_wiggle_adapter
    if hair_wiggle_adapter.status(context).get('active'):
        # begin_export stops transient Hair before freezing its snapshot. Until
        # that happens, this current pose is not the snapshot's dependency proof.
        proof = {**proof, 'fingerprint_known': False, 'fingerprint': '',
                 'proof_reason': 'Active Hair preview changes snapshot inputs; unchanged is unproven.'}
    return _with_rigging(proof, item)


def remember_source_baseline(context, item):
    """Freeze the prepared Source content, not a fabricated export receipt.

    This is called once during Add. Never seed an older row during Scan: its rig
    inputs may already be edited. Unknown dependencies remain explicitly Unknown.
    """
    if _composition_row(item):
        proof = _composition_fingerprint(context, item, source=True)
    else:
        proof = fingerprint_native(item.source_action, item.rig, context.scene, item.source_slot)
        from . import hair_wiggle_adapter
        if hair_wiggle_adapter.status(context).get('active'):
            proof = {**proof, 'fingerprint_known': False, 'fingerprint': '',
                     'proof_reason': 'Active Hair preview changes prepared inputs; unchanged is unproven.'}
        proof = _with_rigging(proof, item, source=True)
    receipt = {'schema': SOURCE_BASELINE_SCHEMA, 'proof': proof,
               'workItemId': getattr(item, 'work_item_id', ''),
               'identity': list(_identity(item, json.loads(item.link_identity))),
               'source_hash': item.source_hash, 'implementation': export_implementation()}
    item.source_baseline_receipt = worklist._encoded(receipt)


def _sha256_value(value):
    return (isinstance(value, str) and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def _composition_metadata(metadata, item=None):
    from . import animation_composition_contract as contract
    if not isinstance(metadata, dict) or set(metadata) != {
            'composition', 'spec_sha256', 'reference_action_sha256', 'reference_packet_sha256'}:
        raise ValueError('Incomplete composition/reference revision proof.')
    spec = (contract.validate_item(metadata['composition'], item) if item is not None
            else contract.validate(metadata['composition']))
    if (metadata['spec_sha256'] != contract.identity(spec)
            or not _sha256_value(metadata['reference_action_sha256'])
            or metadata['reference_packet_sha256'] != spec['referencePacketSha256']):
        raise ValueError('Composition/reference revision proof differs from its DTO.')
    return {**metadata, 'composition': spec}


def _valid_proof(proof):
    if (not isinstance(proof, dict) or proof.get('schema') not in {
            FINGERPRINT_SCHEMA, COMPOSITION_FINGERPRINT_SCHEMA}
            or type(proof.get('fingerprint_known')) is not bool
            or proof['fingerprint_known'] and not _sha256_value(proof.get('fingerprint'))):
        return False
    if 'rigging' in proof or 'base_fingerprint' in proof:
        if (proof['schema'] != FINGERPRINT_SCHEMA or not _valid_rigging_input(proof.get('rigging'))
                or 'base_fingerprint' not in proof):
            return False
        if proof['fingerprint_known']:
            if (not _sha256_value(proof['base_fingerprint'])
                    or proof['fingerprint'] != _rigging_fingerprint(proof['base_fingerprint'], proof['rigging'])):
                return False
        elif proof['base_fingerprint'] != '' or proof.get('fingerprint') != '':
            return False
    if proof['schema'] == COMPOSITION_FINGERPRINT_SCHEMA:
        try:
            _composition_metadata(proof.get('composition_metadata'))
        except (ValueError, TypeError, KeyError):
            return False
        if proof['fingerprint_known'] and not all(_sha256_value(proof.get(key))
                for key in ('authored_b_sha256', 'effective_input_sha256')):
            return False
        if proof['fingerprint_known']:
            from . import animation_composition_contract as contract
            spec = proof['composition_metadata']['composition']
            timing = {key: spec[key] for key in ('timeMode', 'sampleStart', 'sampleEnd',
                                               'fps', 'editSpeed', 'editPhase')}
            if proof['fingerprint'] != contract.digest({
                    'schema': COMPOSITION_FINGERPRINT_SCHEMA,
                    'authored': proof['authored_b_sha256'],
                    'inputs': proof['effective_input_sha256'], 'timing': timing}):
                return False
    return True


def _same_proof(proof, previous):
    if (not _valid_proof(proof) or not _valid_proof(previous)
            or proof.get('fingerprint_known') is not True
            or previous.get('fingerprint_known') is not True
            or proof['schema'] != previous['schema']
            or proof['fingerprint'] != previous['fingerprint']):
        return False
    if 'rigging' in proof or 'rigging' in previous:
        return (proof.get('base_fingerprint') == previous.get('base_fingerprint')
                and proof.get('rigging') == previous.get('rigging'))
    if proof['schema'] == COMPOSITION_FINGERPRINT_SCHEMA:
        return (proof['authored_b_sha256'] == previous['authored_b_sha256']
                and proof['effective_input_sha256'] == previous['effective_input_sha256']
                and proof['composition_metadata'] == previous['composition_metadata'])
    return True


def _source_baseline(item, link, implementation):
    try:
        receipt = json.loads(getattr(item, 'source_baseline_receipt', ''))
        if (not isinstance(receipt, dict) or receipt.get('schema') != SOURCE_BASELINE_SCHEMA
                or not _valid_proof(receipt.get('proof'))
                or receipt.get('workItemId', '') != workspace.work_item_id(link)
                or receipt.get('identity') != list(_identity(item, link))
                or receipt.get('source_hash') != item.source_hash
                or receipt.get('implementation') != implementation):
            return None
        if receipt['proof']['schema'] == COMPOSITION_FINGERPRINT_SCHEMA:
            _composition_metadata(receipt['proof']['composition_metadata'], item)
        elif _composition_row(item):
            return None
        if 'rigging' in receipt['proof']:
            _rigging_capture(item, link, receipt['proof'])
        elif _rigging_row(item):
            return None
        return receipt
    except (AttributeError, TypeError, ValueError, OSError, KeyError, RuntimeError):
        return None


def comparison_receipt(item, link, implementation):
    # A failed/tampered later publication proof must not fall back to the
    # prepared Source and mistakenly treat the prior edits as unchanged.
    if getattr(item, 'last_synced_receipt', ''):
        return _receipt(item, link, implementation)
    return _source_baseline(item, link, implementation)


def unchanged_proof(item, link, implementation, proof):
    receipt = comparison_receipt(item, link, implementation)
    previous = receipt.get('proof', {}) if receipt else {}
    return _same_proof(proof, previous)


def running():
    return _batch is not None and _batch['queue'].busy


def _status(saved, message, error=False):
    saved.status, saved.collection_status, saved.has_error = message, message, error
    saved.collection_feedback = True


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}:
                area.tag_redraw()


def _current_link(saved, item, catalog):
    worklist._check_baseline(item)
    worklist._item(saved, item.item_id)
    if item.custom_action is None or item.custom_action.library is not None:
        raise ValueError('The local editable Custom Action is missing.')
    worklist._slot(item.custom_action, item.custom_slot)
    if _composition_row(item) or getattr(item, 'workspace_path', ''):
        catalog = worklist._fresh_workspace(saved, item)
    elif catalog is None:
        catalog = worklist._fresh_workspace(saved)
    if getattr(item, 'new_animation', False):
        from .animation_worklist_new import validate
        return validate(saved, item, catalog)
    clip = next((entry for entry in catalog['clips']
                 if worklist._clip_key(entry) == item.clip_key), None)
    if (clip is None or clip.get('needsRebuild') or not clip.get('linkManifest')
            or Path(clip['linkManifest']).resolve() != Path(item.manifest_path).resolve()):
        raise ValueError('Prepare this Controller slot in Unity; existing Custom edits are kept.')
    link = links.load_link(item.manifest_path)
    worklist._validate_route(saved, clip, link, item)
    if links._identity(link) != links._identity(json.loads(item.link_identity)):
        raise ValueError('This Link was rebuilt. Keep existing edits and use a separate fresh worklist.')
    return link


def _publication(link):
    """Exact already-verified Link outputs; display names have no authority."""
    return {field: link.get(field) for field in (
        'revision', 'fbxFile', 'fbxSha256', 'metadataFile', 'metadataSha256')}


def _identity(item, link):
    if getattr(item, 'new_animation', False):
        from .animation_worklist_new import identity
        return identity(link)
    return links._identity(link)


def _composition_export(record, metadata, item):
    """Validate the public numeric isolation receipt, never infer it from names."""
    from . import animation_composition_contract as contract
    metadata = _composition_metadata(metadata, item)
    spec = metadata['composition']
    fields = {'schema', 'compositionId', 'editWorkItemId', 'editNewActionId',
              'sourceAction', 'sourceActionSlot', 'isolatedEditAction',
              'referenceActionUnchanged', 'referenceActionBeforeSha256',
              'referenceActionAfterSha256', 'effectiveChannelsSha256',
              'effectiveChannelCount', 'effectiveChannels', 'sampleSpace',
              'excludedReferenceEvaluation'}
    if (not isinstance(record, dict) or set(record) != fields
            or record['schema'] != contract.EXPORT_SCHEMA
            or any(record[key] != spec[key] for key in (
                'compositionId', 'editWorkItemId', 'editNewActionId'))
            or record['referenceActionUnchanged'] is not True
            or record['excludedReferenceEvaluation'] is not True
            or record['sampleSpace'] != 'bone-local'
            or type(record['sourceActionSlot']) is not int or record['sourceActionSlot'] < 0
            or any(not isinstance(record[key], str) or not record[key].strip()
                   for key in ('sourceAction', 'isolatedEditAction'))
            or record['referenceActionBeforeSha256'] != metadata['reference_action_sha256']
            or record['referenceActionAfterSha256'] != metadata['reference_action_sha256']
            or not _sha256_value(record['effectiveChannelsSha256'])):
        raise ValueError('The mandatory isolated B export proof is invalid.')
    channels = record['effectiveChannels']
    if (not isinstance(channels, list) or type(record['effectiveChannelCount']) is not int
            or record['effectiveChannelCount'] != len(channels) or not channels
            or len(channels) > 10240 or any(not isinstance(value, str) for value in channels)
            or len(channels) != len(set(channels))):
        raise ValueError('The effective B channel proof is incomplete or duplicated.')
    # The worker samples all ten TRS components for each authenticated path.
    # Exact membership protects against omissions as well as outside-scope writes.
    expected = {path + '|' + prop + '|' + str(component)
                for path in spec['allowedBonePaths']
                for prop, count in (('localPosition', 3), ('localRotation', 4), ('localScale', 3))
                for component in range(count)}
    if set(channels) != expected:
        raise ValueError('The effective B channels differ from the authenticated Upper Body paths.')
    return record


def _published_composition(item, link, metadata, record, *, current=False):
    """Bind frozen measured proof to physical publication, not mutable UI state."""
    from . import animation_composition_contract as contract
    metadata = _composition_metadata(metadata, item)
    record = _composition_export(record, metadata, item)
    if current and contract.validate_item(link.get('composition'), item) != metadata['composition']:
        raise ValueError('The published Link differs from the frozen composition configuration.')
    path = Path(link['metadataFile'])
    if path.stat().st_size > links.ANIMATION_METADATA_MAX_BYTES:
        raise ValueError('The published composition metadata exceeds 32 MiB.')
    if links._sha256(path) != link.get('metadataSha256'):
        raise ValueError('The published composition metadata file changed after publication.')
    output = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(output, dict) or output.get('composition') != metadata['composition']
            or output.get('composition_export') != record):
        raise ValueError('Published metadata differs from the frozen isolated B proof.')
    return metadata, record


def _rigging_capture(item, link, proof):
    """Read immutable capture/owner fields; later edited curves cannot reseed them."""
    from . import animation_rigging_contract as contract, unity_animation_rigging as rigging
    component = proof['rigging']
    if (not _valid_rigging_input(component) or not _rigging_row(item)
            or component['base_action'] != item.custom_action.name
            or component['base_slot'] != item.custom_slot
            or component['work_item_id'] != workspace.work_item_id(link)
            or component['work_item_id'] != uuid.UUID(item.work_item_id).hex):
        raise ValueError('The frozen Rigging proof belongs to another base Action, slot or WorkItem.')
    capture = contract.validate_capture(rigging.capture_for_action(
        item.custom_action, item.custom_slot, item.work_item_id))
    current = rigging.input_proof(item.rig, item.custom_action, item.custom_slot,
                                  item.work_item_id, source=False)
    if (not _valid_rigging_input(current)
            or any(current[key] != component[key] for key in RIGGING_INPUT_FIELDS
                   if key != 'input_motion_sha256')
            or contract.capture_digest(capture) != component['capture_sha256']
            or capture['newActionId'] != component['new_action_id']
            or capture['targetGuid'] != link.get('targetGuid')):
        raise ValueError('The original Rigging capture or input owner changed after its proof was frozen.')
    return capture


def _published_rigging(item, link, proof, *, expected=None):
    """Bind the strict physical return to frozen ownership, never current input keys."""
    from . import animation_rigging_contract as contract
    capture = _rigging_capture(item, link, proof)
    component = proof['rigging']
    path = Path(link['metadataFile'])
    if (path.stat().st_size > links.ANIMATION_METADATA_MAX_BYTES
            or links._sha256(path) != link.get('metadataSha256')):
        raise ValueError('The published Rigging metadata changed or exceeds its size bound.')
    metadata = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(metadata, dict) or metadata.get('action') != component['base_action']
            or type(metadata.get('actionSlot')) is not int
            or metadata['actionSlot'] != component['base_slot']):
        raise ValueError('The published base Action/slot differs from the frozen Rigging proof.')
    record = contract.validate_export(metadata.get('rigging'), capture,
        source_packet_sha256=component['source_packet_sha256'], work_item_id=component['work_item_id'],
        new_action_id=component['new_action_id'], source_action=component['base_action'],
        source_action_slot=component['base_slot'], target_input_action=component['input_action'],
        target_input_action_slot=component['input_slot'])
    if expected is not None and record != expected:
        raise ValueError('Published Rigging inputs differ from the frozen successful return.')
    return record


def _receipt(item, link, implementation):
    try:
        receipt = json.loads(item.last_synced_receipt)
        proof = receipt.get('proof') if isinstance(receipt, dict) else None
        if not _valid_proof(proof):
            return None
        if (receipt.get('schema') != RECEIPT_SCHEMA
                or receipt.get('workItemId', '') != workspace.work_item_id(link)
                or receipt.get('identity') != list(_identity(item, link))
                or receipt.get('publication') != _publication(link)
                or receipt.get('implementation') != implementation
                or receipt.get('source_hash') != item.source_hash):
            return None
        for file_field, hash_field in (('fbxFile', 'fbxSha256'),
                                       ('metadataFile', 'metadataSha256')):
            path, digest = link.get(file_field), link.get(hash_field)
            if not path or not digest or links._sha256(Path(path)) != digest:
                return None
        if proof['schema'] == COMPOSITION_FINGERPRINT_SCHEMA:
            if receipt.get('composition_metadata') != proof['composition_metadata']:
                return None
            _published_composition(item, link, receipt['composition_metadata'],
                                   receipt.get('composition_export'))
        elif _composition_row(item):
            return None
        if 'rigging' in proof:
            if not isinstance(receipt.get('rigging_metadata'), dict):
                return None
            _published_rigging(item, link, proof, expected=receipt['rigging_metadata'])
        elif _rigging_row(item):
            return None
        return receipt
    except (AttributeError, TypeError, ValueError, OSError, KeyError, RuntimeError):
        return None


def scan_changes(context):
    """Read selected-slot content/dependencies once, without activating Actions."""
    worklist._idle(context)
    saved = worklist.state(context)
    implementation = export_implementation()
    counts = dict.fromkeys(('CHANGED', 'UNCHANGED', 'UNKNOWN', 'BLOCKED'), 0)
    for item in saved.items:
        old_state = item.scan_state
        changes = dict.fromkeys(('b_edited', 'effective_inputs_changed',
                                 'composition_changed', 'export_needed'), False)
        try:
            link = _current_link(saved, item, None)
            proof = item_fingerprint(context, item)
            if not _valid_proof(proof):
                raise ValueError('The animation comparison proof is incomplete.')
            receipt = comparison_receipt(item, link, implementation)
            old_proof = receipt.get('proof', {}) if receipt else {}
            if not proof['fingerprint_known']:
                state, reason = 'UNKNOWN', proof['proof_reason']
            elif not receipt and getattr(item, 'new_animation', False) and link.get('revision') == 0:
                state, reason = 'CHANGED', 'New complete Action; no original version or return exists yet.'
                changes['export_needed'] = True
            elif not receipt or not old_proof.get('fingerprint_known'):
                state, reason = 'UNKNOWN', 'No complete preparation or successful export proof for this comparison.'
            elif proof['schema'] != old_proof.get('schema'):
                state, reason = 'UNKNOWN', 'This row has no matching content-proof schema; unchanged is unproven.'
            elif _same_proof(proof, old_proof):
                state, reason = 'UNCHANGED', 'Matches its prepared Source or last successful publication, including timing and rig inputs.'
                if proof['schema'] == COMPOSITION_FINGERPRINT_SCHEMA:
                    reason = 'B curves, canonical export inputs, and full composition/reference metadata match the proven baseline.'
            elif proof['schema'] == COMPOSITION_FINGERPRINT_SCHEMA:
                changes.update(
                    b_edited=proof['authored_b_sha256'] != old_proof['authored_b_sha256'],
                    effective_inputs_changed=proof['effective_input_sha256'] != old_proof['effective_input_sha256'],
                    composition_changed=proof['composition_metadata'] != old_proof['composition_metadata'],
                    export_needed=True)
                if changes['b_edited']:
                    reason = 'Authored B curves changed; isolated B export and composition publication are required.'
                elif changes['effective_inputs_changed']:
                    reason = 'B export timing or authenticated inputs changed; B curves are unchanged, export is required.'
                else:
                    reason = 'Composition or reference metadata changed; B curves are unchanged, publication refresh is required.'
                state = 'CHANGED'
            else:
                state, reason = 'CHANGED', 'Content, export timing or necessary rig inputs changed since successful Sync.'
                changes['export_needed'] = True
            if (isinstance(proof.get('rigging'), dict) and isinstance(old_proof.get('rigging'), dict)
                    and proof['rigging'] != old_proof['rigging']):
                changes.update(effective_inputs_changed=True, export_needed=True)
                if state == 'UNKNOWN':
                    reason = 'Target inputs changed; complete base dependencies remain unproven. Select explicitly to export.'
                elif state == 'CHANGED':
                    reason = 'Target inputs or their captured ownership changed; a fresh return is required.'
        except (ValueError, RuntimeError, OSError, ReferenceError, TypeError) as exc:
            state, reason = 'BLOCKED', str(exc)
        item.scan_state, item.scan_reason = state, reason
        for field, value in changes.items():
            if hasattr(item, field):
                setattr(item, field, value)
        if state in {'UNCHANGED', 'BLOCKED'}:
            item.sync_selected = False
        elif state == 'UNKNOWN' and old_state != 'UNKNOWN':
            item.sync_selected = False
        elif state == 'CHANGED' and old_state in {'NOT_SCANNED', 'UNCHANGED'}:
            item.sync_selected = True
        # Unknown is never selected automatically. Preserve only an explicit
        # artist selection, including the operator's mandatory rescan.
        counts[state] += 1
    saved.scan_completed = True
    _status(saved, 'Scan: ' + ', '.join(str(counts[key]) + ' ' + key.title() for key in counts) + '.')
    _redraw()
    return counts


def _begin(context, mode, entries):
    global _batch
    worklist._idle(context)
    if context.window is None:
        raise ValueError('Run collection operations in an owning Blender window.')
    queue = SerialQueue(mode, entries)
    if not queue.busy:
        _status(worklist.state(context), 'No selected work to perform.')
        return queue
    batch = {'queue': queue, 'window': context.window, 'scene': context.scene, 'job': None}
    _batch = batch
    try:
        bpy.app.timers.register(_poll_collection, first_interval=0.1)
    except Exception:
        if _batch is batch:
            _batch = None
        raise
    _status(worklist.state(context), ('Adding' if mode == ADD else 'Syncing') +
            ' collection: 0 / ' + str(queue.total) + '.')
    _redraw()
    return queue


def begin_add_ready(context):
    worklist._idle(context)
    saved = worklist.state(context)
    catalog = worklist._fresh_workspace(saved)
    request = workspace.prepare_request(catalog)
    requested = set(request[1]) if request is not None else None
    included = {item.clip_key for item in saved.items}
    entries = [QueueItem(worklist._clip_key(clip)) for clip in catalog['clips']
               if (requested is None or workspace.work_item_id(clip) in requested)
               and clip.get('compositionReferenceSource') is not True and clip.get('sourceOnly') is not True
               and worklist._clip_key(clip) not in included and not clip.get('needsRebuild')
               and clip.get('linkManifest') and Path(clip['linkManifest']).is_file()]
    queue = _begin(context, ADD, entries)
    if not queue.busy:
        return queue
    _batch['prepare_request'] = request
    # Freeze every selected row's route before the first native import. A later
    # request/source/slot change stops the remainder, retaining completed rows.
    _batch['add_routes'] = {worklist._clip_key(clip): (workspace.route_identity(clip), clip['linkManifest'])
                            for clip in catalog['clips'] if worklist._clip_key(clip) in queue.remaining_keys}
    return queue


def begin_sync_changed(context, *, changed_only=False):
    saved = worklist.state(context)
    if not saved.scan_completed:
        raise ValueError('Scan Changes first; Unknown entries require explicit selection.')
    entries = [QueueItem(item.item_id) for item in saved.items
               if (item.scan_state == 'CHANGED' if changed_only else
                   item.sync_selected and item.scan_state in {'CHANGED', 'UNKNOWN'})]
    queue = _begin(context, SYNC, entries)
    if changed_only:
        unresolved = sum(item.scan_state in {'UNKNOWN', 'BLOCKED'} for item in saved.items)
        notice = (str(unresolved) + ' animation(s) need attention and were kept locally.'
                  if unresolved else '')
        if queue.busy:
            _batch['changed_only'] = True
            _batch['skipped_notice'] = notice
        else:
            _status(saved, 'Nothing ready to export. ' + notice if notice else 'No changes to export.')
    return queue


def _record_launch_proof(batch, attempt, item, link, proof):
    if batch.get('changed_only'):
        receipt = comparison_receipt(item, link, export_implementation())
        previous = receipt.get('proof', {}) if receipt else {}
        first_export = getattr(item, 'new_animation', False) and link.get('revision') == 0
        if (not _valid_proof(proof) or proof.get('fingerprint_known') is not True
                or not first_export and (previous.get('fingerprint_known') is not True
                    or previous.get('schema') != proof.get('schema'))):
            raise ValueError('The change comparison is no longer complete; this animation was kept locally.')
        if not first_export and _same_proof(proof, previous):
            raise ValueError('This animation is unchanged; no duplicate was exported.')
    return batch['queue'].record_fingerprint(attempt.token, proof)


def _start(context, batch, attempt):
    queue = batch['queue']
    if queue.mode == ADD:
        saved = worklist.state(context)
        catalog = worklist._fresh_workspace(saved)
        if workspace.prepare_request(catalog) != batch['prepare_request']:
            raise ValueError('The Unity preparation request changed; completed Custom work was kept.')
        clip = next((clip for clip in catalog['clips'] if worklist._clip_key(clip) == attempt.key), None)
        if clip is not None and (clip.get('compositionReferenceSource') is True or clip.get('sourceOnly') is True):
            raise ValueError('A requested row became a read-only A reference; no ordinary edit was imported.')
        if clip is None or (workspace.route_identity(clip), clip.get('linkManifest')) != batch['add_routes'][attempt.key]:
            raise ValueError('A requested work-item source or destination changed; its import was stopped.')
        previous_scene = context.window.scene
        item = worklist.add(context, attempt.key, activate_new=False, _collection=True)
        # Only a successful first import that actually switches scenes created
        # an editing scene; a legacy owned rig can be reused with saved.rig unset.
        if not queue.successes:
            batch['created_scene'] = context.window.scene != previous_scene
        # Subsequent imports use that one rig and leave the artist's active
        # Action untouched.
        batch['scene'] = context.window.scene
        queue.complete(attempt.token, success=True, result={'item_id': item.item_id}, published=True)
    else:
        saved = worklist.state(context)
        item = worklist._item(saved, attempt.key)
        link = _current_link(saved, item, None)
        worklist.activate(context, attempt.key, 'CUSTOM', _collection=True)
        # Sync records its one complete proof before it starts the worker. The
        # queue and receipt use that same snapshot within this event-loop turn.
        job = worklist.sync(context, _collection=True,
            _collection_proof=lambda proof: _record_launch_proof(batch, attempt, item, link, proof))
        job['_collection_token'] = attempt.token
        batch['job'] = job


def _finish(batch):
    global _batch
    queue = batch['queue']
    message = (('Added' if queue.mode == ADD else 'Synced') + ' ' + str(len(queue.successes)) +
               ' / ' + str(queue.total) + '; ' + queue.state.title() + '.')
    if queue.mode == ADD and queue.successes:
        try:
            scene_name = batch['scene'].name
        except ReferenceError:
            pass  # A deleted scene cannot supply a label; queue cleanup still runs.
        else:
            message += ((' Created' if batch.get('created_scene') else ' Used') +
                        ' animation scene "' + scene_name + '".')
    if queue.failure:
        message += ' ' + queue.failure.message
    if queue.cancel_error:
        message += ' ' + queue.cancel_error
    elif queue.cancel_message:
        message += ' ' + queue.cancel_message
    if batch.get('skipped_notice'):
        message += ' ' + batch['skipped_notice']
    try:
        _status(batch['scene'].character_designer_animation_worklist, message,
                bool(queue.failure or queue.cancel_error))
    except ReferenceError:
        pass
    if _batch is batch:
        _batch = None
    _redraw()


def _poll_collection():
    batch = _batch
    if batch is None:
        return None
    queue = batch['queue']
    if not queue.busy:
        _finish(batch)
        return None
    try:
        window = batch['window']
        if window not in tuple(bpy.context.window_manager.windows) or window.scene != batch['scene']:
            cancel(message='The owning window or scene changed; completed items were kept.')
        elif batch['job'] is not None:
            from . import animation_export
            if animation_export.active_job() is None:
                # Someone disposed this worker outside its completion adapter.
                # No result means no receipt and no successful publication claim.
                export_finished(batch['job'], error='The owned export stopped without a publication result.')
        elif queue.running is None:
            from .animation import _link_idle
            if _link_idle(include_collection=False):
                with bpy.context.temp_override(window=window):
                    queue.tick(lambda attempt: _start(bpy.context, batch, attempt))
        if not queue.busy:
            _finish(batch)
            return None
        _status(batch['scene'].character_designer_animation_worklist,
                ('Adding' if queue.mode == ADD else 'Syncing') + ' collection: ' +
                str(len(queue.successes)) + ' / ' + str(queue.total) + '.')
    except (ReferenceError, RuntimeError, ValueError) as exc:
        cancel(message=str(exc))
        if not queue.busy:
            _finish(batch)
            return None
    _redraw()
    return 0.25


def export_finished(job, result=None, error=''):
    """Called only after native publication/disposal (or confirmed cancellation)."""
    scene = job.get('_worklist_scene')
    receipt_error = ''
    batch = _batch
    token = job.get('_collection_token')
    if token is not None:
        attempt = batch['queue'].running if batch is not None else None
        if (batch is None or batch['job'] is not job or attempt is None or attempt.token != token):
            return 'Ignored stale collection completion; no publication proof was recorded.'
    if result is not None and scene is not None:
        try:
            saved = scene.character_designer_animation_worklist
            item = worklist._item(saved, job['_worklist_item_id'])
            if getattr(item, 'new_animation', False):
                from .animation_worklist_new import load
                link = load(item.manifest_path)
            else:
                link = links.load_link(item.manifest_path)
            if (_identity(item, link) != _identity(item, json.loads(item.link_identity))
                    or str(Path(result['link_manifest']).resolve()) != str(Path(item.manifest_path).resolve())
                    or result['revision'] != link.get('revision')
                    or result['fbx_sha256'] != link.get('fbxSha256')
                    or str(Path(result['filepath']).resolve()) != str(Path(link['fbxFile']).resolve())):
                raise ValueError('Published result differs from this worklist Link; no unchanged proof was recorded.')
            proof = job['_worklist_fingerprint']
            if not _valid_proof(proof):
                raise ValueError('The frozen export comparison proof is incomplete.')
            if result.get('unsupported_channels'):
                if proof['schema'] == COMPOSITION_FINGERPRINT_SCHEMA:
                    raise ValueError('The isolated B export omitted mandatory channels; no unchanged proof was recorded.')
                proof = {**proof, 'fingerprint_known': False, 'fingerprint': '',
                         'proof_reason': 'Export omitted unsupported channels; unchanged is unproven.'}
            receipt = {'schema': RECEIPT_SCHEMA, 'proof': proof,
                       'workItemId': workspace.work_item_id(link),
                       'identity': list(_identity(item, link)), 'publication': _publication(link),
                       'implementation': job['_worklist_export_implementation'],
                       'source_hash': item.source_hash}
            if proof['schema'] == COMPOSITION_FINGERPRINT_SCHEMA:
                metadata = job.get('_worklist_composition_metadata')
                if (metadata != proof['composition_metadata']
                        or job.get('_worklist_composition') != metadata['composition']):
                    raise ValueError('The frozen composition/reference metadata differs from the launch proof.')
                metadata, isolated = _published_composition(item, link, metadata,
                    job.get('_worklist_composition_export'), current=True)
                receipt.update(composition_metadata=metadata, composition_export=isolated)
            elif _composition_row(item):
                raise ValueError('A legacy fingerprint cannot prove an explicit composition publication.')
            if 'rigging' in proof:
                receipt['rigging_metadata'] = _published_rigging(item, link, proof)
            elif _rigging_row(item):
                raise ValueError('A base-only fingerprint cannot prove this explicit Rigging publication.')
            item.last_synced_receipt = worklist._encoded(receipt)
            item.scan_state, item.scan_reason = 'NOT_SCANNED', 'Scan to compare current edits with the published snapshot.'
            item.sync_selected, saved.scan_completed = False, False
        except (ValueError, RuntimeError, OSError, ReferenceError, TypeError, KeyError) as exc:
            receipt_error = str(exc)
    if batch is not None and batch['job'] is job:
        accepted = batch['queue'].complete(job['_collection_token'], success=result is not None,
                                          result=result, message=error or receipt_error, published=result is not None)
        if not accepted:
            return 'Ignored stale collection completion.'
        batch['job'] = None
        if receipt_error:
            # Publication is real and remains counted, but fail closed on
            # bookkeeping and do not start another Action.
            batch['queue'].cancel()
            try:
                _status(scene.character_designer_animation_worklist, receipt_error, True)
            except ReferenceError:
                pass
    return receipt_error


def cancel(_context=None, *, message='Collection cancelled; completed items were kept.'):
    batch = _batch
    if batch is None:
        return False

    def cancel_running(attempt):
        from . import animation, animation_export
        job = batch['job']
        if job is not None:
            if animation_export.active_job() is not job:
                raise RuntimeError('The collection lost its native worker owner; wait for completion.')
            animation_export.cancel_export(job)
            if bpy.app.timers.is_registered(animation._poll_action_export):
                bpy.app.timers.unregister(animation._poll_action_export)
            animation._export_window_manager = None
            batch['job'] = None
        batch['queue'].acknowledge_cancel(attempt.token, message=message)

    return batch['queue'].cancel(cancel_running)


def stop():
    """Refresh/load cleanup: no saved queues, surprise launches or shared kills."""
    batch = _batch
    if batch is None:
        return
    cancel(message='Collection stopped for reload; completed items were kept.')
    if not batch['queue'].busy:
        if bpy.app.timers.is_registered(_poll_collection):
            bpy.app.timers.unregister(_poll_collection)
        _finish(batch)
