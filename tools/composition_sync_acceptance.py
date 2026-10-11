"""Explicit root-invoked physical B Sync acceptance; never a numeric-test default.

run() starts the real official worker and advances only exactly allowlisted
prepared Link/NewAction manifests. Call it in the owned disposable Blender
process only after its foreground two-A report passes. Importing this module
starts nothing. It never saves the artist or changes a source packet.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

SCHEMA = 'x.validation.character-composition-sync-fixture/1'
REPORT_SCHEMA = 'x.validation.character-composition-sync/1'
FIELDS = {'schema', 'allowPreparedLinkPublication', 'evidenceDirectory', 'items'}
ITEM_FIELDS = {'kind', 'workItemId', 'newActionId', 'manifestPath', 'manifestSha256'}
NUMERIC_SCHEMA = 'x.validation.character-composition-b-numeric/1'
NUMERIC_FIELDS = {'schema', 'completed', 'authorization', 'provenance', 'spec',
                  'composition_export', 'effective_values'}
PROVENANCE_FIELDS = {'originalPreparedWorkId', 'actualWorkItemId', 'newActionId',
                     'templateLinkId', 'manifestPath', 'manifestSha256', 'modelSha256',
                     'packetSha256', 'sourceAction', 'sourceActionSlot',
                     'publicManifestWorkItemId', 'publicManifestNewActionId', 'sourcePreparedWorkItemIdExact'}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_config(config):
    require(type(config) is dict and set(config) == FIELDS and config['schema'] == SCHEMA
            and config['allowPreparedLinkPublication'] is True,
            'Physical Sync requires its separate explicit publication fixture.')
    require(type(config['evidenceDirectory']) is str and Path(config['evidenceDirectory']).is_absolute()
            and type(config['items']) is list and len(config['items']) in {2, 3},
            'Use a fresh absolute evidence directory and two prepared variants, optionally one genuine new B.')
    seen = set()
    for index, row in enumerate(config['items']):
        require(type(row) is dict and set(row) == ITEM_FIELDS and
                row['kind'] == ('SOURCE' if index < 2 else 'NEW')
                and type(row['manifestPath']) is str and Path(row['manifestPath']).is_absolute()
                and type(row['manifestSha256']) is str and re.fullmatch('[0-9a-f]{64}', row['manifestSha256']),
                'Pin the exact prepared publication path and bytes; no implicit publication targets.')
        item_id = uuid.UUID(row['workItemId']).hex
        require(uuid.UUID(item_id).int and item_id not in seen, 'Actual publication work items must be distinct.')
        seen.add(item_id)
        require(row['newActionId'] == '' if index < 2 else uuid.UUID(row['newActionId']).int != 0,
                'A new publication requires its existing genuine native NewAction identity.')
    return config


def validate_one(row):
    """Exact explicit target for a single real SOURCE or registered NEW Sync."""
    require(type(row) is dict and set(row) == ITEM_FIELDS and row['kind'] in {'SOURCE', 'NEW'}
            and type(row['manifestPath']) is str and Path(row['manifestPath']).is_absolute()
            and type(row['manifestSha256']) is str and re.fullmatch('[0-9a-f]{64}', row['manifestSha256']),
            'A single physical Sync requires its exact actual manifest and pre-Sync bytes.')
    item_id = uuid.UUID(row['workItemId']).hex
    require(int(item_id, 16) != 0, 'The actual WorkItem identity is required.')
    new_id = uuid.UUID(row['newActionId']).hex if row['kind'] == 'NEW' else ''
    require(int(new_id, 16) != 0 if new_id else row['kind'] == 'SOURCE' and row['newActionId'] == '',
            'Only an actual registered NEW row may provide a NewAction identity.')
    return {**row, 'workItemId': item_id, 'newActionId': new_id,
            'manifestPath': str(Path(row['manifestPath']).resolve())}


def validate_independent_expected(row, proof):
    row = validate_one(row)
    require(type(proof) is dict and set(proof) == NUMERIC_FIELDS
            and proof['schema'] == NUMERIC_SCHEMA and proof['completed'] is True
            and validate_one(proof['authorization']) == row,
            'Use the independent complete numeric proof for this exact native publication target.')
    provenance = proof['provenance']
    require(type(provenance) is dict and set(provenance) == PROVENANCE_FIELDS
            and provenance['actualWorkItemId'] == row['workItemId']
            and provenance['newActionId'] == row['newActionId']
            and provenance['manifestPath'] == row['manifestPath']
            and provenance['manifestSha256'] == row['manifestSha256']
            and int(uuid.UUID(provenance['originalPreparedWorkId']).hex, 16) != 0
            and int(uuid.UUID(provenance['templateLinkId']).hex, 16) != 0
            and uuid.UUID(provenance['publicManifestWorkItemId']).hex == row['workItemId']
            and (uuid.UUID(provenance['publicManifestNewActionId']).hex if provenance['publicManifestNewActionId'] else '') == row['newActionId']
            and uuid.UUID(provenance['sourcePreparedWorkItemIdExact']).hex == provenance['originalPreparedWorkId']
            and all(type(provenance[key]) is str and re.fullmatch('[0-9a-f]{64}', provenance[key])
                    for key in ('modelSha256', 'packetSha256'))
            and type(provenance['sourceAction']) is str and provenance['sourceAction']
            and type(provenance['sourceActionSlot']) is int and provenance['sourceActionSlot'] >= 0
            and all(type(proof[key]) is dict for key in ('spec', 'composition_export', 'effective_values')),
            'The numeric report must preserve exact native/template/packet/Action provenance.')
    # Also reject non-plain/nonfinite inputs before any native lookup or launch.
    json.dumps(proof, ensure_ascii=False, allow_nan=False)
    return row


def _services():
    return {name: importlib.import_module('character_designer.' + name) for name in (
        'animation_composition', 'animation_composition_export', 'animation_export',
        'animation_worklist', 'animation_worklist_collection', 'animation_link',
        'animation_worklist_new', 'animation_link_source', 'animation_worklist_fingerprint')}


def _native_row(context, row, modules):
    saved = modules['animation_worklist'].state(context)
    matches = [item for item in saved.items
               if uuid.UUID(item.work_item_id).hex == row['workItemId']]
    require(len(matches) == 1, 'Exactly one existing native B row is required.')
    item = matches[0]
    require(item.rig is saved.rig and item.new_animation == (row['kind'] == 'NEW')
            and (uuid.UUID(item.new_action_id).hex if item.new_animation else '') == row['newActionId']
            and str(Path(item.manifest_path).resolve()) == row['manifestPath']
            and file_hash(item.manifest_path) == row['manifestSha256'],
            'Actual native row, NewAction or manifest differs from explicit authorization.')
    return item


def foreground_one(context, item):
    """Read-only actual B proof, useful after Loop authoring or real Raise registration.

    This starts no process and publishes nothing. The immutable plain report is
    remeasured by run_one before its explicit worker launch.
    """
    modules = _services()
    backend, export = modules['animation_composition'], modules['animation_composition_export']
    require(not backend.active(item.rig), 'Close the owned editing session before the independent numeric proof.')
    row = validate_one({'kind': 'NEW' if item.new_animation else 'SOURCE',
        'workItemId': item.work_item_id, 'newActionId': item.new_action_id if item.new_animation else '',
        'manifestPath': item.manifest_path, 'manifestSha256': file_hash(item.manifest_path)})
    require(_native_row(context, row, modules).item_id == item.item_id,
            'Foreground proof requires the unique actual row.')
    session = backend._export_session(context, item)
    packet = backend._packet_for_item(item)
    if item.new_animation:
        receipt = modules['animation_worklist_new'].authenticate_composition_bind_template(
            context, item.rig, session['spec'], packet)
        template = modules['animation_link'].load_link(receipt['link_path'])
        publication = modules['animation_worklist_new'].load(item.manifest_path)
    else:
        template = modules['animation_link'].load_link(item.manifest_path)
        publication = template
    original_id = uuid.UUID(packet['composition']['editWorkItemId']).hex
    require(uuid.UUID(template['workItemId']).hex == original_id,
            'The original prepared WorkItem must come from the sealed native template Link.')
    provenance = {'originalPreparedWorkId': original_id, 'actualWorkItemId': row['workItemId'],
        'newActionId': row['newActionId'], 'templateLinkId': uuid.UUID(template['linkId']).hex,
        'manifestPath': row['manifestPath'], 'manifestSha256': row['manifestSha256'],
        'modelSha256': item.model_sha256, 'packetSha256': packet['_sha256'],
        'sourceAction': item.custom_action.name, 'sourceActionSlot': item.custom_slot,
        'publicManifestWorkItemId': publication['workItemId'],
        'publicManifestNewActionId': publication['newActionId'] if item.new_animation else '',
        'sourcePreparedWorkItemIdExact': packet['composition']['editWorkItemId']}
    with export.isolate_export(context, session, item) as inputs:
        result = {'schema': NUMERIC_SCHEMA, 'completed': True, 'authorization': row,
            'provenance': provenance, 'spec': session['spec'],
            'composition_export': inputs['composition_export'], 'effective_values': inputs['effective_values']}
        result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
    validate_independent_expected(row, result)
    return result


def archive_worker(job, destination):
    """Copy durable diagnostics before ordinary poll/cancel removes its temp root."""
    if job.get('log'):
        job['log'].flush()
    destination = Path(destination)
    require(not destination.exists(), 'Worker evidence must never overwrite another attempt.')
    shutil.copytree(job['root'], destination)
    return {path.relative_to(destination).as_posix(): file_hash(path)
            for path in sorted(destination.rglob('*')) if path.is_file()}


def run_one(context, row, evidence_directory, independent_expected, *, before_worker=None):
    """Explicitly publish one real prepared Loop or registered NEW Raise.

    A fresh immutable foreground_one proof is mandatory. This API cannot
    manufacture a two-A test or a collected Hold. Native publication and its
    receipt are reread after the official worker completes; no Unity Collect
    is implied by a successful Blender report.
    """
    import bpy
    row = validate_independent_expected(row, independent_expected)
    require(type(evidence_directory) is str and Path(evidence_directory).is_absolute(),
            'Use a separate absolute single-worker evidence directory.')
    evidence = Path(evidence_directory).resolve()
    require(not evidence.exists(), 'Use a fresh physical Sync evidence directory.')
    evidence.mkdir(parents=True)
    report_path = evidence / 'composition_sync_report.json'
    report = {'schema': REPORT_SCHEMA, 'completed': False, 'worker_launch_performed': False,
              'scope': 'One explicitly allowed actual native publication on the disposable copy.',
              'kind': row['kind'], **independent_expected['provenance']}
    def persist():
        report_path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    modules = _services()
    composition, export, exporter, worklist, collector, links, new, source, fingerprint = (
        modules[name] for name in ('animation_composition', 'animation_composition_export',
            'animation_export', 'animation_worklist', 'animation_worklist_collection',
            'animation_link', 'animation_worklist_new', 'animation_link_source',
            'animation_worklist_fingerprint'))
    native = importlib.import_module('verify_character_composition_native')
    saved = worklist.state(context)
    protected_actions = native.action_proofs(bpy, fingerprint)
    job, archived, captured = None, False, None
    try:
        require(exporter.active_job() is None and not composition.active(saved.rig),
                'Finish the previous job/session before explicitly launching this serial worker.')
        item = _native_row(context, row, modules)
        measured = foreground_one(context, item)
        require(measured == independent_expected,
                'B numeric values, configuration or native provenance changed after its independent proof.')
        report['spec'] = measured['spec']
        report['foreground_effective_channels_sha256'] = measured['composition_export']['effectiveChannelsSha256']
        captured, old_context = worklist._capture_rig(context, item.rig), source._context_state(context)
        old_index = saved.active_index
        persist()
        if before_worker is not None:
            require(callable(before_worker), 'The explicit per-worker resource guard must be callable.')
            before_worker(context, item)
        print('COMPOSITION_PHYSICAL_SYNC_START ' + row['workItemId'], flush=True)
        job = composition.begin_sync(context, item)
        report['worker_launch_performed'] = True
        # Only this exact job's redundant scheduled poll is disabled.
        if bpy.app.timers.is_registered(composition._poll_sync):
            bpy.app.timers.unregister(composition._poll_sync)
        while job['process'].poll() is None:
            if time.monotonic() - job['started'] > 600:
                raise TimeoutError('The official composition worker exceeded its existing timeout.')
            time.sleep(.1)
        report['worker_evidence'] = archive_worker(job, evidence / 'worker_1')
        archived = True
        result = export.poll_isolated_export(job)
        require(result is not None, 'The official isolated export returned no publication result.')
        receipt_error = collector.export_finished(job, result=result)
        report['collection_receipt_error'] = receipt_error
        require(not receipt_error, receipt_error)
        published = new.load(item.manifest_path) if item.new_animation else links.load_link(item.manifest_path)
        fbx, metadata = Path(published['fbxFile']), Path(published['metadataFile'])
        require(fbx.is_file() and fbx.stat().st_size > 0 and file_hash(fbx) == published['fbxSha256']
                and metadata.is_file() and file_hash(metadata) == published['metadataSha256'],
                'Actual published FBX/sidecar hashes differ from the final manifest.')
        payload = json.loads(metadata.read_text(encoding='utf-8'))
        export.validate_export_receipt(payload, measured['spec'], job['composition_export'], payload.get('effective_values'))
        require(payload['effective_values'] == measured['effective_values']
                and payload['composition_export']['effectiveChannelsSha256'] == measured['composition_export']['effectiveChannelsSha256']
                and json.loads(item.last_synced_receipt)['composition_export'] == payload['composition_export'],
                'Published worker values or native receipt differ from independently proved B.')
        report.update(manifest_after_sha256=file_hash(item.manifest_path), revision=published['revision'],
            fbx_file=str(fbx), fbx_sha256=published['fbxSha256'], metadata_file=str(metadata),
            metadata_sha256=published['metadataSha256'], effective_values=payload['effective_values'],
            composition_export=payload['composition_export'], receipt=json.loads(item.last_synced_receipt))
        native.assert_actions(protected_actions, fingerprint)
        report.update(completed=True, actual_fbx_and_metadata_hashes_verified=True,
                      physical_values_equal_independent_foreground=True,
                      independent_fbx_reimport_performed=False)
        print('COMPOSITION_PHYSICAL_SYNC_END ' + row['workItemId'], flush=True)
        return report
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        try:
            if job is not None and exporter.active_job() is job:
                if not archived:
                    report['worker_evidence'] = archive_worker(job, evidence / 'worker_1')
                export.cancel_isolated_export(job)
        finally:
            if captured is not None:
                worklist._restore_rig(context, item.rig, captured)
                source._restore_context(context, old_context, playing=True)
                saved.active_index = old_index
            persist()


def run(context, config, foreground_report, *, before_worker=None):
    """Explicit two-A physical proof; its numeric fixture remains mandatory."""
    validate_config(config)
    require(foreground_report.get('numeric_completed') is True
            and foreground_report.get('same_b_across_two_a_seek_and_object_motion') is True
            and foreground_report.get('fresh_native_associations_verified') is True,
            'Physical two-A Sync requires the successful fresh-model foreground fixture first.')
    evidence = Path(config['evidenceDirectory']).resolve()
    require(not evidence.exists(), 'Use a fresh physical Sync evidence directory.')
    evidence.mkdir(parents=True)
    report = {'schema': REPORT_SCHEMA, 'completed': False, 'items': [], 'worker_launch_performed': False}
    path = evidence / 'composition_sync_report.json'
    foreground_ids = [uuid.UUID(row['work_item_id']).hex for row in foreground_report['fresh_packet_proofs']]
    require([uuid.UUID(row['workItemId']).hex for row in config['items'][:2]] == foreground_ids,
            'The physical workers must use the two exact foreground-authenticated variants.')
    modules = _services()
    try:
        for index, raw_row in enumerate(config['items']):
            row = validate_one(raw_row)
            item = _native_row(context, row, modules)
            measured = foreground_one(context, item)
            if index < 2:
                require(measured['effective_values'] == foreground_report['effective_values'],
                        'B changed after the independent two-A foreground test.')
            attempt = run_one(context, row, str(evidence / ('item_' + str(index + 1))), measured,
                              before_worker=before_worker)
            report['items'].append(attempt)
            report['worker_launch_performed'] = True
        require(report['items'][0]['effective_values'] == report['items'][1]['effective_values'],
                'Physical B publication changes under the two genuine A variants.')
        report.update(completed=True, physical_values_equal_foreground_and_two_a=True,
                      actual_fbx_and_metadata_hashes_verified=True, independent_fbx_reimport_performed=False)
        return report
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
        raise
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')


def main():
    """Separate explicit entry: foreground fixture, then authorized real Sync."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--sync-config', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    native = importlib.import_module('verify_character_composition_native')
    fixture = native.validate_fixture(json.loads(Path(args.fixture).read_text(encoding='utf-8')))
    config = validate_config(json.loads(Path(args.sync_config).read_text(encoding='utf-8')))
    require(fixture['sessionChecks'] is None,
            'Run physical unchanged-B proof before a separate UI fixture intentionally edits B.')
    evidence = Path(fixture['evidenceDirectory']).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    report_path = evidence / 'composition_native_report.json'
    require(not report_path.exists(), 'Preserve the prior foreground report; use fresh evidence.')
    foreground = {'schema': native.REPORT_SCHEMA, 'completed': False,
                  'fixture': str(Path(args.fixture).resolve()),
                  'fixture_sha256': native.file_hash(args.fixture)}
    try:
        native.run(fixture, foreground,
            after_numeric=lambda context, proof: run(context, config, proof))
    finally:
        report_path.write_text(json.dumps(foreground, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
