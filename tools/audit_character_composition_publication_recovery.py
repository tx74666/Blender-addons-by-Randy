"""Audit an actually completed publication recovery using files only.

No Blender, SDK, CIM, process launch or scene save is performed. The caller must
supply the Root-recorded natural exit and actual launch PID. Old failed reports
stay failed; numerical cases, authoring and publications are explicitly inherited.
The optional closure writer records these already verified Root exit facts; it
does not obtain a new OS inventory or authorize a GUI/native launch.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import uuid

ROOT = Path(r'D:/Blender/Projects/Character/X/validation/composition_20261011')
SCHEMA = 'x.validation.character-composition-publication-recovery-completed-file-audit/1'
CLOSURE_SCHEMA = 'x.validation.character-composition-publication-recovery-completion-closure/1'
RECOVERY_SHA = 'a21e531819225c4990242351ee9a2e84646cd8ac8a6714c5cd845c4a56f61fcf'
LEGACY_SHA = '25d5860875ea4f18b45f8206243ddc6940e576aa3e98a00c49aa11ad5be76a4b'
ARTIST_SHA = 'e8d3013ef14e02ed76160a838e7d255490fd1b6ece340e7c3f9e33dc57a3377f'
COUNTS = {'numeric_cases_inherited': 12, 'numeric_cases_reexecuted': 0,
    'fresh_isolated_variants_inherited': 2, 'fresh_isolated_variants_executed': 0,
    'physical_source_publications_inherited': 3, 'physical_source_publications_executed': 0,
    'current_receipt_acceptances': 1, 'current_loop_imports': 1, 'current_UI_imports_executed': 2,
    'native_authoring_executed': 0, 'numeric_resamples_executed': 0, 'worker_exports_executed': 0}


def require(value, message):
    if not value:
        raise ValueError(message)


def checksum(value):
    require(type(value) is str and re.fullmatch('[0-9a-fA-F]{64}', value), 'Require a complete SHA-256.')
    return value.lower()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pin(path, expected=None, size=None, ledger=None):
    path = Path(path)
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), 'Missing absolute regular pinned file: ' + str(path))
    path = path.resolve()
    row = {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}
    require(expected is None or row['sha256'] == checksum(expected), 'Pinned file changed: ' + str(path))
    require(size is None or type(size) is int and size == row['bytes'], 'Pinned file byte count changed: ' + str(path))
    if ledger is not None:
        require(row['path'] not in ledger or ledger[row['path']] == row,
                'An input changed after its first admission: ' + str(path))
        ledger[row['path']] = row
    return row


def record(row, ledger=None):
    require(type(row) is dict and {'path', 'sha256'} <= set(row)
            and set(row) <= {'path', 'sha256', 'bytes', 'file_count', 'changed_product_files'}, 'Unsupported file pin.')
    return pin(row['path'], row['sha256'], row.get('bytes'), ledger)


def read(row, ledger=None):
    row = record(row, ledger)
    require(row['bytes'] <= 32 * 1024 * 1024, 'JSON exceeds the 32 MiB animation-sidecar bound.')
    value = json.loads(Path(row['path']).read_text(encoding='utf-8-sig'),
        parse_constant=lambda value: require(False, 'Nonfinite JSON: ' + value))
    json.dumps(value, allow_nan=False)
    return value


def module(row, name, ledger=None):
    spec = importlib.util.spec_from_file_location(name, record(row, ledger)['path'])
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def same_pin(left, right):
    return Path(left['path']).resolve() == Path(right['path']).resolve() and checksum(left['sha256']) == checksum(right['sha256'])


def identifier(value):
    require(type(value) is str, 'Require the actual UUID string.')
    result = uuid.UUID(value)
    require(result.int != 0, 'A UUID cannot be zero.')
    return result.hex


def true_fields(value, fields, message):
    require(all(value.get(key) is True for key in fields), message)


def validate_ack(recovery, config, legacy, ledger):
    """One fresh RNA acknowledgement of the unchanged archived publication."""
    loop = recovery['loop_physical']
    author, job, link = [read(config[key], ledger) for key in ('loopAuthorReport', 'workerJob', 'publishedLink')]
    private = job['composition_job']
    worker_result = read(config['workerResult'],ledger)
    require(worker_result['ok'] is True and worker_result['source_curve_count'] == 405
            and type(worker_result['bones']) is list and len(worker_result['bones']) == len(set(worker_result['bones'])) == 217
            and set(worker_result['bones']) == set(private['scope']['mapping']['indices'])
            and worker_result['samples'] == 298 and worker_result['unsupported_channels'] == []
            and all(worker_result[key] == private[key] for key in ('composition','composition_export','effective_values')),
            'Inherited worker result must retain the actual full217/405 curves/410 by 298 values.')
    require(recovery['loop_authoring'] == author and author['complete'] is True,
            'The original completed authoring proof must remain inherited verbatim.')
    operator = legacy.require_loop_operator_proof(author)
    require(loop.get('completed') is True and loop.get('kind') == 'SOURCE' and loop.get('newActionId') == ''
            and loop.get('revision') == link['revision'] == 1 and loop.get('publicationInherited') is True
            and loop.get('source_export_executed') is False and loop.get('fresh_receipt_acceptance_count') == 1
            and loop.get('archived_launch_fingerprint_reconstructed') is False
            and loop.get('result_wrapper_is_new_recovery_event') is True
            and loop.get('physical_numeric_values_inherited') is True and not loop.get('collection_receipt_error'),
            'Require a successful fresh acknowledgement, never a fictional archived launch or new export.')
    true_fields(loop, ('complete_worker_scope_equal', 'recovered_selected_curves_exact',
        'protected_outside_curves_unchanged', 'protected_Source_unchanged', 'protected_A_unchanged',
        'inner_composition_closed', 'temporary_owned_edit_draft_disposed', 'global_Action_inventory_restored'),
        'The exact native transfer/inner Close/protected Action proof is incomplete.')
    require(not loop.get('archive_cleanup_errors') and not loop.get('transfer_failed'),
            'A failed transfer or incomplete Action cleanup cannot become an accepted acknowledgement.')
    require(all(loop.get(key) is False for key in ('transform_or_key_authoring_performed',
                'samples_reconstructed', 'numeric_resampling_performed'))
            and loop['selected_curve_count'] == 405 and loop['effective_channel_count'] == 410
            and loop['sample_count'] == 298 and loop['native_clock_verified'] == 60
            and loop['full_custom_before_sha256'] == author['authoring']['before_sha256']
            == '8f4aa620fc3c35afc47627cd82de14773ff1398a74484dba53076d94c063a11e'
            and loop['full_custom_after_sha256'] == author['authoring']['after_sha256']
            == '294db6aa173971d8ca472e9ffb498d521ee7f408395cf9ef68588ddcfa25f0c9',
            'Keep exact 405-curve/full Custom provenance and zero new authoring/sampling.')
    require(loop['spec'] == private['composition'] == author['numeric_proof']['spec'] == link['composition']
            and loop['composition_export'] == private['composition_export'] == author['numeric_proof']['composition_export']
            and loop['effective_values'] == private['effective_values'] == author['numeric_proof']['effective_values']
            and identifier(loop['actualWorkItemId']) == identifier(link['workItemId'])
            == identifier(loop['spec']['editWorkItemId']), 'Recovery changed the exact sealed DTO or inherited numeric payload.')
    effective_sha = legacy.numeric_payload(loop['composition_export'], loop['effective_values'], loop['spec'])
    require(len(loop['effective_values']['timesSeconds']) == 298, 'Require all 410 by 298 published samples.')
    archive = loop['archive_action']
    require(same_pin(archive, config['workerSnapshot']) and archive['name'] == job['action']
            and archive['slot'] == loop['archived_public_source_slot'] == job['action_slot'] == 929201142
            and Path(archive['library']).resolve() == Path(config['workerSnapshot']['path']).resolve(),
            'The linked curve Action must be the exact read-only worker archive and actual archived slot.')
    canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    require(loop['worker_scope_sha256'] == hashlib.sha256(canonical(private['scope'])).hexdigest()
            and loop['structural_proof_sha256'] == hashlib.sha256(canonical(private['scope']['structural_proof'])).hexdigest(),
            'Complete native worker Scope/Rest structural proof changed.')
    for name in ('curve_payload_sha256', 'selected_native_curve_payload_sha256'):
        checksum(loop[name])
    scoped = loop['fresh_scoped_fingerprint']
    require(scoped == loop['archive_scoped_fingerprint'] and scoped['fingerprint_known'] is True,
            'Fresh selected curves must retain the complete archived fingerprint.')
    receipt, proof = loop['current_last_synced_receipt'], loop['fresh_receipt_proof']
    require(proof['fingerprint_known'] is True and all(proof[key] == scoped[key]
            for key in ('authored_b_sha256', 'effective_input_sha256'))
            and receipt['proof'] == proof and receipt['implementation'] == loop['fresh_receipt_implementation']
            and receipt['composition_metadata'] == proof['composition_metadata']
            and proof['composition_metadata']['composition'] == loop['spec']
            and proof['composition_metadata']['reference_action_sha256'] == loop['composition_export']['referenceActionBeforeSha256']
            and receipt['composition_export'] == loop['composition_export']
            and receipt['publication'] == {key: link.get(key) for key in ('revision', 'fbxFile', 'fbxSha256', 'metadataFile', 'metadataSha256')},
            'Fresh collector fingerprint/implementation/publication receipt changed.')
    wrapper = loop['existing_publication_result_wrapper']
    require(Path(wrapper['link_manifest']).resolve() == Path(config['publishedLink']['path']).resolve()
            and wrapper['revision'] == 1 and wrapper['unsupported_channels'] == []
            and Path(wrapper['filepath']).resolve() == Path(link['fbxFile']).resolve()
            and checksum(wrapper['fbx_sha256']) == checksum(link['fbxSha256']), 'The new result wrapper refers to another publication.')
    for which in ('import_anchor_before_transfer', 'current_recovered_anchor'):
        anchor = loop[which]
        require(anchor['row']['item_id'] == loop['current_item_id'] and anchor['spec'] == loop['spec']
                and anchor['custom_action'] == loop['current_custom_action']
                and anchor['custom_slot'] == loop['current_custom_slot'] == loop['actual_current_native_slot']
                and type(anchor['custom_native_pointer']) is int and anchor['custom_native_pointer'] > 0
                and anchor['fingerprint']['fingerprint_known'] is True,
                'Actual Loop import/recovered Action anchor differs from the native acknowledgement.')
    require(loop['import_anchor_before_transfer']['custom_native_pointer'] == loop['current_recovered_anchor']['custom_native_pointer'],
            'The original Custom Action was replaced during recovery.')
    outputs = {'manifest': record(config['publishedLink'], ledger),
        'fbx': pin(link['fbxFile'], link['fbxSha256'], ledger=ledger),
        'metadata': pin(link['metadataFile'], link['metadataSha256'], ledger=ledger)}
    metadata = read(outputs['metadata'], ledger)
    require(all(metadata[key] == loop[key] for key in ('composition_export', 'effective_values'))
            and metadata['composition'] == loop['spec'], 'Published sidecar bytes differ from the inherited actual worker values.')
    return {'work_item_id': link['workItemId'], 'kind': 'SOURCE', 'revision': 1, **outputs,
            'effective_channels_sha256': effective_sha, 'publication_inherited': True,
            'fresh_receipt_acceptance': True}, operator


def validate_current_rows(numeric, persisted, seed, specs):
    rows = numeric['currentImportedForUI']
    require(len(rows) == len(seed['itemIds']) == len(seed['workItemIds']) == 2
            and len(set(seed['itemIds'])) == 2 and numeric['fresh_packet_proofs'] == persisted['fresh_packet_proofs']
            and numeric['fresh_associations'] == persisted['fresh_associations'], 'Separate actual current UI rows from inherited isolated proofs.')
    for index, (row, item_id, work_id, spec) in enumerate(zip(rows, seed['itemIds'], seed['workItemIds'], specs)):
        current, previous = row['packet_rest_proof'], persisted['fresh_packet_proofs'][index]
        require(identifier(row['item_id']) == identifier(item_id)
                and identifier(current['work_item_id']) == identifier(work_id) == identifier(spec['editWorkItemId'])
                and {key:value for key,value in current.items() if key != 'mapping_error_metres'}
                == {key:value for key,value in previous.items() if key != 'mapping_error_metres'}
                and current['native_count'] == 217 and type(current['mapping_error_metres']) in (int,float)
                and math.isfinite(current['mapping_error_metres']) and 0 <= current['mapping_error_metres'] <= .0002
                and row['association'] == persisted['fresh_associations'][index]
                and all(row[key] is False for key in ('numeric_resampled','isolated_sync_executed','source_export_executed')),
                'Current UI packet/native217 Rest/association must match stable inherited proof without sampling.')
        anchor = row['import_anchor']
        require(identifier(anchor['row']['item_id']) == identifier(item_id) and anchor['spec'] == spec
                and anchor['fingerprint']['fingerprint_known'] is True and row['b_fingerprint']['fingerprint_known'] is True,
                'The actual UI row lacks its immediately captured original native Action anchor.')
        case = next(case for case in numeric['cases'] if case['reference'] == 'a' + str(index+1))
        require(row['b_fingerprint'] == case['b_fingerprint'], 'Current UI B differs from the actual original twelve-case input.')


def audit(*, config, config_sha256, source_entry, source_entry_sha256, expected_pid,
          process_exit, process_exit_sha256, launch_receipt, launch_receipt_sha256,
          legacy_builder, legacy_builder_sha256=LEGACY_SHA):
    ledger = {}
    config_pin = pin(config, config_sha256, ledger=ledger)
    entry_pin = pin(source_entry, source_entry_sha256, ledger=ledger)
    cfg, entry = read(config_pin, ledger), read(entry_pin, ledger)
    evidence = Path(cfg['evidenceDirectory']).resolve()
    require(evidence == ROOT/'n16' and Path(config_pin['path']) == evidence/'recovery_config.json'
            and Path(entry_pin['path']) == evidence/'source_entry_receipt.json'
            and entry['schema'] == 'x.validation.character-composition-publication-recovery-source-entry/1'
            and entry['source_only'] is True and entry['native_execution_performed'] is False
            and same_pin(entry['config'],config_pin), 'Admit only this actual fresh n16 recovery source/config.')
    recovery_tool = record(entry['driver'], ledger)
    require(recovery_tool['sha256'] == RECOVERY_SHA, 'Use the exact independently reviewed recovery implementation.')
    recovery_api = module(recovery_tool, '_completed_publication_recovery_api', ledger)
    require(set(cfg) == recovery_api.FIELDS and cfg['schema'] == recovery_api.SCHEMA, 'Recovery fixture fields changed.')
    require(len(entry['driver_source_reviews']) == 2
            and cfg['tools']['verify_character_composition_native']['sha256'] == recovery_api.NATIVE_SHA
            and cfg['tools']['run_character_composition_first_stage_continuation']['sha256'] == recovery_api.CONTINUATION_SHA,
            'Require both independent source approvals and the unchanged native/continuation APIs.')
    for row in entry['driver_source_reviews']:
        review = read(row,ledger)
        require(review['passed'] is True and review['source_only'] is True and review['native_execution_performed'] is False
                and review['source_files']['run_character_composition_publication_recovery.py'] ==
                {**recovery_tool, 'path': str(Path(review['source_files']['run_character_composition_publication_recovery.py']['path']).resolve())}
                and sha(review['source_files']['run_character_composition_publication_recovery.py']['path']) == RECOVERY_SHA,
                'Recovery owner and peer must bind the exact canonical source bytes.')
    legacy_pin = pin(legacy_builder, legacy_builder_sha256, ledger=ledger)
    require(legacy_pin['sha256'] == LEGACY_SHA, 'Keep the preserved old UI builder immutable.')
    legacy = module(legacy_pin, '_recovery_pure_legacy_numeric_helpers', ledger)
    native = module(cfg['tools']['verify_character_composition_native'], '_recovery_completed_native_helpers', ledger)
    fixture = native.validate_fixture(read(cfg['nativeFixture'],ledger))
    budget = recovery_api.budget_projection(cfg,fixture)
    require(same_pin(entry['runtime_projection'],budget['python_projection'])
            and same_pin(entry['full_projection'],budget['complete_candidate'])
            and same_pin(entry['sidecar_budget_projection_receipt'],cfg['sidecar_budget_projection_receipt']), 'Actual projection/source ancestry changed.')
    for key in recovery_api.INPUT_SHAS:
        require(checksum(cfg[key]['sha256']) == recovery_api.INPUT_SHAS[key], 'Inherited recovery input digest changed: ' + key)
        record(cfg[key],ledger)
    record(cfg['failedProtection'],ledger)
    protection = read(cfg['failedProtection'],ledger)
    require(protection['completed'] is False and protection['official_collection_acceptance_completed'] is False
            and protection['full_outer_native_recovery_completed'] is False and protection['owned_qa_seed_created'] is False,
            'Never relabel the old n15 failure, collector failure or missing seed as completed.')
    report_pins = {name: pin(evidence/file,ledger=ledger) for name,file in
        (('recovery','publication_recovery_report.json'),('numeric','publication_recovery_native_report.json'))}
    recovery, numeric = [read(report_pins[name],ledger) for name in ('recovery','numeric')]
    require(recovery['schema'] == recovery_api.REPORT_SCHEMA and recovery['completed'] is True
            and recovery['native_execution_performed'] is True and same_pin(recovery['fixture'],config_pin)
            and recovery['numeric'] == numeric and recovery['inherited_first_stage_completed'] is False
            and not recovery.get('error') and all(type(recovery.get(key)) is int and recovery[key] == value for key,value in COUNTS.items()),
            'Require the actual completed fresh recovery and explicit inherited/executed counts.')
    true_fields(recovery,('full_outer_native_recovery_completed','original_scene_recovery_verified','existing_publications_byte_unchanged'),
                'Fresh outer recovery and all three immutable publications must actually close.')
    require(recovery['artist_save_performed'] is False and recovery['source_packets_rewritten'] is False,
            'Recovery cannot save the artist or rewrite Source packets.')
    old,persisted,physical = [read(cfg[key],ledger) for key in ('inheritedNumericReport','persistedNumericReport','inheritedPhysicalReport')]
    require(old['completed'] is False and persisted['completed'] is False and recovery['two_a_physical'] == physical
            and numeric['cases'] == old['cases'] == persisted['cases']
            and numeric['effective_values'] == old['effective_values'] == persisted['effective_values'], 'Inherited case/value files must remain exact and failed ancestors remain failed.')
    reuse = numeric['numeric_reuse']
    require(reuse['schema'] == 'x.validation.character-composition-publication-recovery-numeric-reuse/1'
            and reuse['old_report_completed'] is False and reuse['cases_reexecuted'] is False
            and reuse['fresh_isolated_variants_inherited'] == 2 and reuse['fresh_isolated_variants_executed'] == 0
            and reuse['source'] == cfg['inheritedNumericReport'] and reuse['persisted_fresh_native_source'] == cfg['persistedNumericReport']
            and reuse['sidecar_budget_projection_receipt'] == cfg['sidecar_budget_projection_receipt'], 'Numerical reuse counts/source pins changed.')
    specs = [read(row,ledger) for row in fixture['specFiles']]
    legacy.require_numeric_cases(numeric,fixture,specs)
    require(numeric['schema'] == 'x.validation.character-composition-native/1'
            and len(numeric['effective_values']['timesSeconds']) == 4,
            'The twelve inherited numeric cases retain the complete actual four-sample payload.')
    true_fields(numeric,('current_ui_native_associations_verified','fresh_inner_recovery_verified'), 'Fresh UI associations/inner restoration did not complete.')
    # The standalone numeric fixture remains the fresh recovery input, not its n11 ancestor.
    require(Path(numeric['fixture']).resolve() == Path(cfg['nativeFixture']['path']).resolve()
            and checksum(numeric['fixture_sha256']) == checksum(cfg['nativeFixture']['sha256']), 'Fresh numeric fixture pin changed.')
    seed = recovery['qa_seed']
    require(seed == numeric['qa_seed'] and Path(seed['seedBlend']).resolve() == Path(cfg['qaSeedFile']).resolve()
            and Path(seed['seedBlend']).resolve().parent == evidence and same_pin(
                {'path':seed['physicalReport'],'sha256':seed['physicalReportSha256']},cfg['inheritedPhysicalReport']),
            'Only the actual saved fresh QA seed with the genuine two-SOURCE physical report is admissible.')
    validate_current_rows(numeric,persisted,seed,specs)
    seed_pin = pin(seed['seedBlend'],seed['seedBlendSha256'],ledger=ledger)
    workspace = read(pin(seed['workspacePath'],seed['workspaceSha256'],ledger=ledger),ledger)
    require(Path(seed['workspacePath']).resolve() == Path(fixture['compositionWorkspace']).resolve()
            and checksum(seed['workspaceSha256']) == checksum(fixture['compositionWorkspaceSha256'])
            and workspace['targetGuid'] == seed['targetGuid'] and workspace['modelSha256'] == seed['modelSha256']
            == fixture['compositionModelSha256'], 'First UI must precede Parent workspace/Collect/Prepare mutation.')
    legacy.published_sources(physical,lambda path,expected=None:pin(path,expected,ledger=ledger))
    ack,operator = validate_ack(recovery,cfg,legacy,ledger)
    publications = []
    for row in physical['items']:
        publications.append({'work_item_id':row['actualWorkItemId'],'kind':'SOURCE','revision':1,
            'manifest':pin(row['manifestPath'],row['manifest_after_sha256'],ledger=ledger),
            'fbx':pin(row['fbx_file'],row['fbx_sha256'],ledger=ledger),
            'metadata':pin(row['metadata_file'],row['metadata_sha256'],ledger=ledger),
            'effective_channels_sha256':row['composition_export']['effectiveChannelsSha256'],
            'publication_inherited':True,'fresh_receipt_acceptance':False})
    publications.append(ack)
    require(len({identifier(row['work_item_id']) for row in publications}) == 3, 'Three distinct real SOURCE publications are required.')
    exited_pin = pin(process_exit,process_exit_sha256,ledger=ledger)
    launch_pin = pin(launch_receipt,launch_receipt_sha256,ledger=ledger)
    exited,launch = read(exited_pin,ledger),read(launch_pin,ledger)
    require(type(expected_pid) is int and expected_pid > 0 and expected_pid != 27676
            and launch['schema'] == 'x.validation.character-composition-native-launch/1'
            and launch['attempt'] == exited['attempt'] == 'n16' and launch['pid'] == exited['pid'] == expected_pid
            and launch['background'] is True and launch['guiGranted'] is False and launch['artistMutationAllowed'] is False
            and Path(launch['config']).resolve() == Path(config_pin['path'])
            and checksum(launch['configSha256']) == config_pin['sha256']
            and same_pin(launch['sourceEntry'],entry_pin), 'Actual once-granted launch/config/source/PID proof differs.')
    grant = read(pin(launch['grant'],launch['grantSha256'],ledger=ledger),ledger)
    require(grant['schema'] == 'randomrealm.character-composition-native-window/1' and grant['attempt'] == 'n16'
            and grant['ownerThreadId'] == '01a12305-aada-7273-b72c-54a41aad77e4'
            and grant['recipientThreadId'] == '01a12751-d08f-7630-b790-18db26fe3186'
            and grant['backgroundAllowed'] is True and grant['headfulAllowed'] is False
            and grant['sourceDeploymentAllowed'] is False and grant['artistMutationAllowed'] is False
            and all(grant[key] is True for key in ('buildingExplicitlyReleased','consoleHeavyHeld','rootUnityGuiHeavyHeld'))
            and Path(grant['configPath']).resolve() == Path(config_pin['path'])
            and checksum(grant['configSha256']) == config_pin['sha256']
            and checksum(grant['sourceEntrySha256']) == entry_pin['sha256']
            and checksum(grant['pythonProjectionSha256']) == checksum(entry['runtime_projection']['sha256'])
            and checksum(grant['fullProjectionSha256']) == checksum(entry['full_projection']['sha256']), 'Actual grant scope/projection pins differ.')
    require(exited['schema'] == 'x.validation.character-composition-natural-process-exit/1'
            and type(exited['naturalExitCode']) is int and exited['naturalExitCode'] == 0
            and exited['forcedTermination'] is False and exited['allQaBlenderWorkersGoneVerified'] is True
            and exited['osInventoryError'] is None and exited['launchReceiptError'] is None
            and exited['observedExitUtc'], 'Require natural exit0 and Root actual all-owned-workers-gone proof.')
    remaining = exited['remainingBlenderProcesses']
    require(type(remaining) is list and len(remaining) == 1 and remaining[0]['ProcessId'] == 27676
            and Path(remaining[0]['ExecutablePath']).resolve() == Path(r'D:/Blender5.2/blender.exe').resolve(), 'Only the protected artist Blender may remain.')
    artist = record(protection['artist_file'],ledger)
    require(artist['sha256'] == ARTIST_SHA == fixture['inputBlendSha256'], 'The protected artist bytes changed.')
    require(type(entry['source_inputs']) is dict and entry['source_inputs'], 'Missing complete actual source-input closure.')
    for path,expected in entry['source_inputs'].items():
        pin(path,expected,ledger=ledger)
    for path,row in list(ledger.items()):
        pin(path,row['sha256'],row['bytes'])
    return {'schema':SCHEMA,'status':'PASS','completed':True,'attempt':'n16',
        'native_execution_performed_by_audit':False,'config':config_pin,'source_entry':entry_pin,
        'reports':report_pins,'process_exit':exited_pin,'launch_receipt':launch_pin,
        'pid':expected_pid,'natural_exit_code':0,'all_qa_workers_gone_verified':True,
        'remaining_blender_processes':remaining,'artist':artist,
        'python_projection':record(entry['runtime_projection'],ledger),
        'full_projection':record(entry['full_projection'],ledger),
        'sidecar_budget_projection_receipt':record(cfg['sidecar_budget_projection_receipt'],ledger),
        **COUNTS,'publications':publications,'qa_seed':seed_pin,'seed_receipt':seed,
        'loop_authoring_inherited':True,'loop_operator_proof_inherited':operator,
        'fresh_inner_and_outer_recovery_verified':True,'protected_actions_and_assets_verified':True,
        'immutable_inputs':ledger,'os_scope':'Root-recorded exit inventory; this audit performs no OS query.',
        'gui_launch_permission_inferred':False}


def write_closure(audit_pin, destination):
    completed = read(audit_pin)
    require(completed['schema'] == SCHEMA and completed['status'] == 'PASS' and completed['completed'] is True
            and completed['natural_exit_code'] == 0 and completed['all_qa_workers_gone_verified'] is True,
            'A Root completion closure requires the successful actual file audit and natural OS exit proof.')
    for row in completed['immutable_inputs'].values():
        record(row)
    require(Path(destination).resolve() == Path(completed['config']['path']).resolve().parent/'completion_closure.json',
            'Write only the fresh owned completion closure for this exact actual recovery attempt.')
    result = {'schema':CLOSURE_SCHEMA,'completed':True,'attempt':'n16','actual_completed_file_audit':record(audit_pin),
        **{key:completed[key] for key in ('config','source_entry','python_projection','full_projection','artist',
            'sidecar_budget_projection_receipt','process_exit','launch_receipt','pid','natural_exit_code',
            'all_qa_workers_gone_verified','remaining_blender_processes')},
        **COUNTS,'qa_seed_provisional_until_this_closure':False,'gui_launch_permission_inferred':False}
    exclusive_json(destination,result)
    return result


def exclusive_json(destination,value):
    destination = Path(destination)
    require(destination.is_absolute() and destination.parent.is_dir() and not destination.exists(), 'Use a fresh absolute owned output file.')
    text = json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n'
    with destination.open('x',encoding='utf-8') as stream:
        stream.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config','source-entry','process-exit','launch-receipt','legacy-builder'):
        parser.add_argument('--'+name,required=True)
        parser.add_argument('--'+name+'-sha256',required=True)
    parser.add_argument('--expected-pid',type=int,required=True)
    parser.add_argument('--audit-out',required=True)
    parser.add_argument('--closure-out',help='Optional Root-owned exclusive closure after the successful actual audit.')
    args = parser.parse_args()
    result = audit(**{key:getattr(args,key) for key in ('config','config_sha256','source_entry','source_entry_sha256',
        'process_exit','process_exit_sha256','launch_receipt','launch_receipt_sha256','legacy_builder','legacy_builder_sha256','expected_pid')})
    exclusive_json(args.audit_out,result)
    if args.closure_out:
        write_closure(pin(args.audit_out),args.closure_out)
    print(json.dumps({'schema':SCHEMA,'status':'PASS','audit':pin(args.audit_out)},indent=2))


if __name__ == '__main__':
    main()
