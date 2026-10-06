"""Explicit serial collection operations; drawing never scans or starts work.

Only the owning window/scene may advance a batch. A native export must publish
and dispose before another Action is activated. Receipts describe the frozen
successful publication, never whatever the artist edited while it ran.
"""

import json
import hashlib
from pathlib import Path

import bpy

from . import animation_link as links, animation_worklist as worklist
from .animation_worklist_fingerprint import SCHEMA as FINGERPRINT_SCHEMA, fingerprint_native
from .animation_worklist_queue import ADD, SYNC, QueueItem, SerialQueue


RECEIPT_SCHEMA = 'character-designer.animation-worklist-receipt/1'
_batch = None


def export_implementation():
    """Explicit scan/launch IO only; receipts also bind the export semantics."""
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        'animation_export.py', 'animation_export_worker.py', 'unity_export_worker.py',
        'animation_worklist_fingerprint.py', 'dress_export_snapshot.py')}


def item_fingerprint(context, item):
    proof = fingerprint_native(item.custom_action, item.rig, context.scene, item.custom_slot)
    from . import hair_wiggle_adapter
    if hair_wiggle_adapter.status(context).get('active'):
        # begin_export stops transient Hair before freezing its snapshot. Until
        # that happens, this current pose is not the snapshot's dependency proof.
        proof = {**proof, 'fingerprint_known': False, 'fingerprint': '',
                 'proof_reason': 'Active Hair preview changes snapshot inputs; unchanged is unproven.'}
    return proof


def running():
    return _batch is not None and _batch['queue'].busy


def _status(saved, message, error=False):
    saved.status, saved.collection_status, saved.has_error = message, message, error


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
    clip = next((entry for entry in catalog['clips']
                 if worklist._clip_key(entry) == item.clip_key), None)
    if (clip is None or clip.get('needsRebuild') or not clip.get('linkManifest')
            or Path(clip['linkManifest']).resolve() != Path(item.manifest_path).resolve()):
        raise ValueError('Prepare this Controller slot in Unity; existing Custom edits are kept.')
    link = links.load_link(item.manifest_path)
    if links._identity(link) != links._identity(json.loads(item.link_identity)):
        raise ValueError('This Link was rebuilt. Keep existing edits and use a separate fresh worklist.')
    return link


def _publication(link):
    """Exact already-verified Link outputs; display names have no authority."""
    return {field: link.get(field) for field in (
        'revision', 'fbxFile', 'fbxSha256', 'metadataFile', 'metadataSha256')}


def _receipt(item, link, implementation):
    try:
        receipt = json.loads(item.last_synced_receipt)
        proof = receipt.get('proof') if isinstance(receipt, dict) else None
        if (not isinstance(proof, dict) or proof.get('schema') != FINGERPRINT_SCHEMA
                or type(proof.get('fingerprint_known')) is not bool
                or (proof['fingerprint_known'] and (not isinstance(proof.get('fingerprint'), str)
                    or len(proof['fingerprint']) != 64
                    or any(char not in '0123456789abcdef' for char in proof['fingerprint'])))):
            return None
        if (receipt.get('schema') != RECEIPT_SCHEMA
                or receipt.get('identity') != list(links._identity(link))
                or receipt.get('publication') != _publication(link)
                or receipt.get('implementation') != implementation
                or receipt.get('source_hash') != item.source_hash):
            return None
        for file_field, hash_field in (('fbxFile', 'fbxSha256'),
                                       ('metadataFile', 'metadataSha256')):
            path, digest = link.get(file_field), link.get(hash_field)
            if not path or not digest or links._sha256(Path(path)) != digest:
                return None
        return receipt
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def scan_changes(context):
    """Read selected-slot content/dependencies once, without activating Actions."""
    worklist._idle(context)
    saved = worklist.state(context)
    catalog = worklist._fresh_workspace(saved)
    implementation = export_implementation()
    counts = dict.fromkeys(('CHANGED', 'UNCHANGED', 'UNKNOWN', 'BLOCKED'), 0)
    for item in saved.items:
        old_state = item.scan_state
        try:
            link = _current_link(saved, item, catalog)
            proof = item_fingerprint(context, item)
            receipt = _receipt(item, link, implementation)
            old_proof = receipt.get('proof', {}) if receipt else {}
            if not proof['fingerprint_known']:
                state, reason = 'UNKNOWN', proof['proof_reason']
            elif not receipt or not old_proof.get('fingerprint_known'):
                state, reason = 'UNKNOWN', 'No complete successful publication proof. Select explicitly to sync.'
            elif proof['fingerprint'] == old_proof.get('fingerprint'):
                state, reason = 'UNCHANGED', 'Matches the last successful publication, including timing and rig inputs.'
            else:
                state, reason = 'CHANGED', 'Content, export timing or necessary rig inputs changed since successful Sync.'
        except (ValueError, RuntimeError, OSError, ReferenceError, TypeError) as exc:
            state, reason = 'BLOCKED', str(exc)
        item.scan_state, item.scan_reason = state, reason
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
    included = {item.clip_key for item in saved.items}
    entries = [QueueItem(worklist._clip_key(clip)) for clip in catalog['clips']
               if worklist._clip_key(clip) not in included and not clip.get('needsRebuild')
               and clip.get('linkManifest') and Path(clip['linkManifest']).is_file()]
    return _begin(context, ADD, entries)


def begin_sync_changed(context):
    saved = worklist.state(context)
    if not saved.scan_completed:
        raise ValueError('Scan Changes first; Unknown entries require explicit selection.')
    entries = [QueueItem(item.item_id) for item in saved.items
               if item.sync_selected and item.scan_state in {'CHANGED', 'UNKNOWN'}]
    return _begin(context, SYNC, entries)


def _start(context, batch, attempt):
    queue = batch['queue']
    if queue.mode == ADD:
        item = worklist.add(context, attempt.key, activate_new=False, _collection=True)
        # The first import owns a new independent scene. Subsequent imports use
        # that one rig and leave the artist's active Action untouched.
        batch['scene'] = context.window.scene
        queue.complete(attempt.token, success=True, result={'item_id': item.item_id}, published=True)
    else:
        saved = worklist.state(context)
        item = worklist._item(saved, attempt.key)
        _current_link(saved, item, worklist._fresh_workspace(saved))
        worklist.activate(context, attempt.key, 'CUSTOM', _collection=True)
        # Sync records its one complete proof before it starts the worker. The
        # queue and receipt use that same snapshot within this event-loop turn.
        job = worklist.sync(context, _collection=True,
            _collection_proof=lambda proof: queue.record_fingerprint(attempt.token, proof))
        job['_collection_token'] = attempt.token
        batch['job'] = job


def _finish(batch):
    global _batch
    queue = batch['queue']
    message = (('Added' if queue.mode == ADD else 'Synced') + ' ' + str(len(queue.successes)) +
               ' / ' + str(queue.total) + '; ' + queue.state.title() + '.')
    if queue.failure:
        message += ' ' + queue.failure.message
    if queue.cancel_error:
        message += ' ' + queue.cancel_error
    elif queue.cancel_message:
        message += ' ' + queue.cancel_message
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
            link = links.load_link(item.manifest_path)
            if (str(Path(result['link_manifest']).resolve()) != str(Path(item.manifest_path).resolve())
                    or result['revision'] != link.get('revision')
                    or result['fbx_sha256'] != link.get('fbxSha256')
                    or str(Path(result['filepath']).resolve()) != str(Path(link['fbxFile']).resolve())):
                raise ValueError('Published result differs from this worklist Link; no unchanged proof was recorded.')
            proof = job['_worklist_fingerprint']
            if result.get('unsupported_channels'):
                proof = {**proof, 'fingerprint_known': False, 'fingerprint': '',
                         'proof_reason': 'Export omitted unsupported channels; unchanged is unproven.'}
            receipt = {'schema': RECEIPT_SCHEMA, 'proof': proof,
                       'identity': list(links._identity(link)), 'publication': _publication(link),
                       'implementation': job['_worklist_export_implementation'],
                       'source_hash': item.source_hash}
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
