"""Root-only receipt-to-fixture builder; import performs no generation/native work.

Supply the actual completed evidence name and explicit config/source-entry/
completion-closure SHA pins. --write only generates new source fixtures after
all receipt admission passes; it never consumes or grants a native/GUI window.
The separate launcher requires a fresh actual headful grant. Failed or missing
attempts cannot produce a fixture. Importing this module does no admission or
generation; no Blender/runtime, subprocess or SDK API is used here.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import struct
import uuid

ROOT = Path(r'D:\Blender\Projects\Character\X\validation\composition_20261011')
TOOLS_RECEIPT_SHA256 = '7ec4592f0c35038d6bc38253e3dc6eb681f8cb119415145f46f8d6f8453ef5f7'
SESSION_SHA256 = '47cd966e74c50188ddbebd1efe8b915dd9df645184ee7409bf657e756cee6fb3'
SOURCE_REVIEW_SHA256 = '5b75fcad568a7ae630eac8efcab77a545bd3707d32f9467f79fd13f93c36d490'
TOOL_NAMES = {'run_character_composition_first_stage', 'verify_character_composition_native',
    'composition_sync_acceptance', 'composition_timing_acceptance',
    'composition_session_acceptance', 'composition_raise_authoring',
    'run_character_composition_raise_stage', 'run_character_composition_ui_fixture',
    'composition_ui_checkpoints', 'release_projection'}
MAX_JSON_BYTES = 32 * 1024 * 1024
SOURCE_PROOF_FIELDS = {'runtime_projection', 'full_projection', 'embedded_overlay_manifest',
    'parent_root_manifest', 'parent_deploy_compile_receipt', 'parent_prepare_receipt',
    'hold_pose_source_constancy_proof', 'native_source_report', 'tools_source_manifest'}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= MAX_JSON_BYTES, 'Missing/bounded JSON receipt: ' + str(path))
    return json.loads(path.read_text(encoding='utf-8-sig'),
        parse_constant=lambda value:require(False, 'Nonfinite JSON receipt value: ' + value))


def pin(path, expected=None):
    require(type(path) is str or isinstance(path, Path), 'A receipt must supply an actual file path.')
    path = Path(path)
    require(path.is_absolute() and path.is_file(), 'Missing absolute receipt file: ' + str(path))
    path = path.resolve()
    digest = sha(path)
    if expected is not None:
        require(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected)
                and digest == expected, 'Pinned immutable file changed: ' + str(path))
    return {'path': str(path), 'sha256': digest}


def identifier(value):
    require(type(value) is str, 'Use actual public work identities from the sealed receipt.')
    result = uuid.UUID(value)
    require(result.int != 0, 'A work identity cannot be empty.')
    return result.hex


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frozen_api(root=ROOT, *, receipt_path=None, receipt_sha256=None):
    require(receipt_path is not None and receipt_sha256 == TOOLS_RECEIPT_SHA256,
            'Supply the explicitly approved frozen05 receipt and full digest; no old/latest fallback.')
    receipt_path = Path(receipt_path)
    directory = receipt_path.resolve().parent
    require(directory.parent == root.resolve() and directory.name == 'tools_headful_05'
            and receipt_path.name == 'frozen_tools_receipt.json',
            'Use the exact root-owned frozen headful receipt outside mutable UI evidence.')
    receipt_pin = pin(receipt_path, receipt_sha256)
    receipt = read(receipt_pin['path'])
    require(receipt.get('schema') == 'x.validation.character-composition-tools-frozen/1'
            and type(receipt.get('files')) is dict and set(receipt['files']) == TOOL_NAMES,
            'Use the exact ten-tool immutable headful receipt.')
    pin(receipt['source_manifest']['path'], receipt['source_manifest']['sha256'])
    require(receipt['files']['composition_session_acceptance']['sha256'] == SESSION_SHA256
            and receipt.get('independent_source_review', {}).get('sha256') == SOURCE_REVIEW_SHA256,
            'The UI requires the reviewed Edit A full-C and positive A upper-B witnesses.')
    pin(receipt['independent_source_review']['path'], SOURCE_REVIEW_SHA256)
    for name, record in receipt['files'].items():
        source = pin(record['path'], record['sha256'])
        require(Path(source['path']) == (directory / (name + '.py')).resolve(),
                'Frozen tool receipt escaped its exact sibling directory: ' + name)
    runner = load(receipt['files']['run_character_composition_ui_fixture']['path'], '_first_ui_frozen_runner')
    native = load(receipt['files']['verify_character_composition_native']['path'], '_first_ui_frozen_native')
    checkpoints = load(receipt['files']['composition_ui_checkpoints']['path'], '_first_ui_frozen_checkpoints')
    return receipt_pin, receipt['files'], runner, native, checkpoints


def require_loop_receipt(author, physical):
    """Bind the third actual SOURCE publication to its independent raw B proof."""
    proof = author.get('numeric_proof')
    require(author.get('schema') == 'x.validation.character-composition-loop-authoring/1'
            and author.get('complete') is True
            and all(author.get(key) is True for key in ('only_explicit_HoldLoop_Custom_authored',
                'all_other_Customs_Sources_unchanged', 'prior_state_restored'))
            and author.get('authoring', {}).get('native_FK_transform_and_keys') is True
            and author['authoring'].get('packet_DTO_Source_A_unchanged') is True
            and author['authoring'].get('before_sha256') != author['authoring'].get('after_sha256'),
            'Require the third Loop actual native FK transform/key/commit and protected original data.')
    require(type(proof) is dict and proof.get('schema') == 'x.validation.character-composition-b-numeric/1'
            and proof.get('completed') is True
            and physical.get('schema') == 'x.validation.character-composition-sync/1'
            and physical.get('completed') is True and physical.get('kind') == 'SOURCE'
            and physical.get('newActionId') == ''
            and physical.get('actual_fbx_and_metadata_hashes_verified') is True
            and physical.get('physical_values_equal_independent_foreground') is True,
            'The third Hold must be an actual SOURCE publication equal to its independent native proof.')
    authorization, provenance, spec = proof['authorization'], proof['provenance'], proof['spec']
    require(authorization.get('kind') == 'SOURCE' and authorization.get('newActionId') == ''
            and provenance.get('newActionId') == '' and spec.get('timeMode') == 'HoldLoop'
            and spec.get('editNewActionId') == ''
            and identifier(physical['actualWorkItemId']) == identifier(authorization['workItemId'])
            == identifier(provenance['actualWorkItemId']) == identifier(spec['editWorkItemId'])
            and physical['spec'] == spec and physical['composition_export'] == proof['composition_export']
            and physical['effective_values'] == proof['effective_values'],
            'The actual third SOURCE Loop identity/spec/complete B payload differs from its independent authoring proof.')
    numeric_payload(proof['composition_export'], proof['effective_values'], spec)
    require(any(any(value != numbers[0] for value in numbers[1:])
                for _name, numbers in proof['effective_values']['channels']),
            'The third actual Loop must contain effective moving B values, not a constant Hold.')


def numeric_payload(proof, values, spec):
    """Rehash the complete published values using the frozen float32 policy."""
    names = sorted(path + '|' + prop + '|' + str(index) for path in spec['allowedBonePaths']
        for prop, count in (('localPosition', 3), ('localRotation', 4), ('localScale', 3))
        for index in range(count))
    require(names and proof.get('schema') == 'cdesigner.animation-composition-export/1'
        and all(proof[key] == spec[key] for key in ('compositionId','editWorkItemId','editNewActionId'))
        and proof['sampleSpace'] == 'bone-local' and proof['referenceActionUnchanged'] is True
        and proof['excludedReferenceEvaluation'] is True
        and proof['referenceActionBeforeSha256'] == proof['referenceActionAfterSha256']
        and type(proof['sourceActionSlot']) is int and proof['sourceActionSlot'] >= 0
        and proof['effectiveChannels'] == names and proof['effectiveChannelCount'] == len(names),
        'Require the exact isolated B identity, all native TRS channels and A exclusion proof.')
    require(type(values) is dict and set(values) == {'schema','timesSeconds','channels'}
        and values['schema'] == 'cdesigner.composition-effective-values/1'
        and [row[0] for row in values['channels']] == names,
        'The complete canonical effective value payload is required.')
    times = values['timesSeconds']
    require(2 <= len(times) <= 20000 and all(type(value) in (int,float) and math.isfinite(value) for value in times)
        and all(left < right for left,right in zip(times,times[1:]))
        and abs(times[0] - spec['sampleStart']) <= 1e-12 and abs(times[-1] - spec['sampleEnd']) <= 1e-12,
        'The raw B sample interval or sample times changed.')
    channels = []
    for name, numbers in values['channels']:
        require(len(numbers) == len(times), 'Every native channel requires the full sample array.')
        canonical = []
        for number in numbers:
            require(type(number) in (int,float) and math.isfinite(number), 'Nonfinite B numeric sample.')
            value = struct.unpack('<f', struct.pack('<f', number))[0]
            require(math.isfinite(value), 'B numeric sample exceeds float32.')
            canonical.append(0. if value == 0. else value)
        channels.append([name,canonical])
    payload = {'schema':values['schema'],'timesSeconds':times,'channels':channels}
    digest = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),
        ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()
    require(digest == proof['effectiveChannelsSha256'], 'Complete effective numeric payload SHA differs from its proof.')
    return digest


def require_numeric_cases(numeric, original, specs):
    require(all(numeric.get(key) is True for key in ('completed','numeric_completed',
        'same_b_across_two_a_seek_and_object_motion','fresh_native_associations_verified',
        'protected_prior_actions_unchanged','protected_assets_unchanged','historical_recovery_association_unchanged'))
        and numeric.get('fresh_workspace_cleanup') == 'historical_native_state_and_rows_restored'
        and numeric.get('worker_launch_performed') is False and original['sessionChecks'] is None
        and original['expectedNativeCount'] == 217,
        'Use completed independent numeric proofs with exact full217 and historical recovery.')
    expected = {(label,obj,seconds) for label in ('a1','a2')
        for obj in range(len(original['objectMotionCases'])) for seconds in original['seekSeconds']}
    cases = numeric['cases']
    actual = [(row['reference'],row['object_motion_case'],row['seek_seconds']) for row in cases]
    require(len(expected) == len(cases) == len(set(actual)) == 12 and set(actual) == expected,
        'Require all twelve distinct two-A/seek/Object-motion native cases.')
    require(len(numeric['fresh_packet_proofs']) == len(numeric['fresh_associations']) == 2
        and all(row['native_count'] == 217 for row in numeric['fresh_packet_proofs'])
        and all(numeric[key]['full_scope_verified'] is True and numeric[key]['native_count'] == 217
            for key in ('qa_fk_preflight','qa_fk_post_adoption')),
        'Both actual packet routes and official FK proofs must retain all217 native bones.')
    reference_hashes = {}
    for row in cases:
        index = 0 if row['reference'] == 'a1' else 1
        require(row['native_count'] == 217
            and identifier(row['b_work_item_id']) == identifier(original['bWorkItemIds'][index]),
            'Native case uses another prepared B or incomplete skeleton.')
        numeric_payload(row['composition_export'], numeric['effective_values'], specs[index])
        reference_hashes.setdefault(row['reference'],set()).add(row['composition_export']['referenceActionBeforeSha256'])
    require(len(reference_hashes['a1']) == len(reference_hashes['a2']) == 1
        and reference_hashes['a1'] != reference_hashes['a2']
        and any(next(row['visible_c_sha256'] for row in cases if (row['reference'],row['object_motion_case'],row['seek_seconds']) == ('a1',0,seconds))
            != next(row['visible_c_sha256'] for row in cases if (row['reference'],row['object_motion_case'],row['seek_seconds']) == ('a2',0,seconds))
            for seconds in original['seekSeconds']), 'The genuine A inputs must have different native content and visible C.')


def require_completion_closure(closure, first_stage, config_pin, source_pin, record_pin):
    audit = closure.get('actual_completed_file_audit', {})
    require(closure.get('schema') == 'x.validation.character-composition-completion-closure/1'
        and closure.get('attempt') == first_stage and closure.get('completed') is True
        and closure.get('source_entry') == source_pin
        and closure.get('all_qa_workers_gone_verified') is True and closure.get('artist_mutation_performed') is False
        and audit.get('schema') == 'x.validation.character-composition-completed-file-audit/1'
        and audit.get('status') == 'PASS' and audit.get('read_only') is True
        and audit.get('natural_exit_code') == 0 and audit.get('actual_os_pid_absent') is True
        and audit.get('numeric_cases') == 12 and audit.get('two_a_count') == 2
        and audit.get('full_native_count') == 217 and audit.get('current_fresh_associations') == 2
        and audit.get('historical_original_recovery') == 'historical_native_state_and_rows_restored'
        and audit.get('artist_input_unchanged') is True
        and len(audit.get('publications', [])) == 3 and all(row['kind'] == 'SOURCE' for row in audit['publications']),
        'Require the actual complete three-SOURCE closure, natural exit0 and all-worker absence proof.')
    require(audit.get('config') == config_pin and audit.get('source_entry') == source_pin
        and closure.get('immutable_source_proofs') == audit.get('immutable_source_proofs')
        and set(audit['immutable_source_proofs']) == SOURCE_PROOF_FIELDS,
        'Completion audit must bind the exact actual config/source entry and immutable source proofs.')
    for row in audit['immutable_source_proofs'].values(): record_pin(row['path'],row['sha256'])
    proofs = audit['immutable_source_proofs']
    raw = read(proofs['hold_pose_source_constancy_proof']['path'])
    require(raw.get('schema') == 'x.validation.character-composition-hold-source-constancy/1'
        and raw.get('source_only') is True and raw.get('native_execution_performed') is False
        and raw.get('source_measurement_completed') is True and raw.get('constant_source_pass') is True,
        'Require the actual independent prepared Hold source constancy proof.')
    for key, field in (('runtime_projection','python_projection'),('full_projection','full_projection')):
        require(closure[field] == proofs[key], 'Closure changed its actual runtime/full projection proof.')
        record_pin(closure[field]['path'],closure[field]['sha256'])
    reports = audit['reports']
    for row in reports.values(): record_pin(row['path'],row['sha256'])
    launch, exited = read(reports['launch']['path']), read(reports['process_exit']['path'])
    require(launch.get('schema') == 'x.validation.character-composition-native-launch/1'
        and launch.get('background') is True and launch.get('artistMutationAllowed') is False
        and launch.get('guiGranted') is False and launch.get('configSha256') == config_pin['sha256']
        and exited.get('schema') == 'x.validation.character-composition-natural-process-exit/1'
        and type(audit.get('pid')) is int and audit['pid'] > 0 and exited.get('pid') == launch.get('pid') == audit['pid']
        and type(exited.get('naturalExitCode')) is int and exited['naturalExitCode'] == 0
        and exited.get('forcedTermination') is False and exited.get('observedExitUtc'),
        'Use the actual naturally exited owned main process; no forced or pending launch proof.')
    artist = record_pin(closure['artist']['path'],closure['artist']['sha256'])
    remaining = closure['remaining_blender_processes']
    remaining = remaining if isinstance(remaining,list) else [remaining]
    require(len(remaining) == 1 and type(remaining[0].get('ProcessId')) is int
        and remaining[0]['ProcessId'] != audit['pid'] and remaining[0].get('ExecutablePath')
        and artist['path'], 'The all-worker closure must retain only the separately protected artist process.')
    return audit


def require_completed_stage(stage, numeric, physical):
    require(stage.get('schema') == 'x.validation.character-composition-first-stage-report/1'
            and stage.get('completed') is True and stage.get('original_scene_recovery_verified') is True
            and stage.get('artist_save_performed') is False and stage.get('source_packets_rewritten') is False
            and stage.get('numeric') == numeric and stage.get('two_a_physical') == physical
            and stage.get('qa_seed') == numeric.get('qa_seed')
            and stage.get('loop_authoring', {}).get('complete') is True
            and stage.get('loop_physical', {}).get('completed') is True
            and stage['loop_physical'].get('actual_fbx_and_metadata_hashes_verified') is True,
            'Wait for the complete genuine three-Hold first stage and exact historical recovery.')
    require_loop_receipt(stage['loop_authoring'], stage['loop_physical'])


def first_hold(specs, original, seed):
    require(len(specs) == 2 and len(original['bWorkItemIds']) == 2 and len(seed['workItemIds']) == 2,
            'The seed must contain the actual two independent A variants.')
    for spec, work_id, saved_id in zip(specs, original['bWorkItemIds'], seed['workItemIds']):
        require(spec.get('schema') == 'cdesigner.animation-composition/1'
                and spec.get('timeMode') == 'HoldPose' and spec.get('editNewActionId') == ''
                and identifier(spec['editWorkItemId']) == identifier(work_id) == identifier(saved_id),
                'Only the exact genuine prepared SOURCE HoldPose variants can seed the first UI.')
    # Preserve literal prepared public UUID formatting; do not synthesize IDs.
    return specs[0]['editWorkItemId']


def empty_evidence(evidence):
    if not evidence.exists():
        return
    require(evidence.is_dir(), 'Owned UI evidence must be a directory.')
    profiles = [evidence / 'CONFIG', evidence / 'DATAFILES']
    require(all(child.is_dir() and child in profiles for child in evidence.iterdir()),
            'Only empty owned CONFIG/DATAFILES directories may precede this UI fixture.')
    require(all(child.is_dir() for child in evidence.rglob('*')),
            'Keep prior UI files; use a new owned evidence directory.')


def published_sources(physical, record_pin, *, expected_count=2):
    require(len(physical.get('items', [])) == expected_count, 'Require the complete expected SOURCE publication list.')
    for row in physical['items']:
        require(row.get('completed') is True and row.get('kind') == 'SOURCE' and not row.get('newActionId')
                and row.get('actual_fbx_and_metadata_hashes_verified') is True
                and row.get('physical_values_equal_independent_foreground') is True,
                'Each prepared Hold must have a complete real physical publication.')
        manifest_path = record_pin(row['manifestPath'], row['manifest_after_sha256'])['path']
        manifest = read(manifest_path)
        require(type(manifest.get('revision')) is int and manifest['revision'] > 0
                and manifest['revision'] == row['revision'], 'Physical Hold revision changed after its proof.')
        for path_key, hash_key, manifest_path_key, manifest_hash_key in (
                ('fbx_file','fbx_sha256','fbxFile','fbxSha256'),
                ('metadata_file','metadata_sha256','metadataFile','metadataSha256')):
            record_pin(row[path_key], row[hash_key])
            require(Path(manifest[manifest_path_key]).resolve() == Path(row[path_key]).resolve()
                    and manifest[manifest_hash_key] == row[hash_key], 'Published SOURCE file identity differs from its receipt.')
        metadata = read(row['metadata_file'])
        require(metadata.get('composition') == row['spec']
                and metadata.get('composition_export') == row['composition_export']
                and metadata.get('effective_values') == row['effective_values'],
                'Actual published Hold metadata differs from the independent native B proof.')
        require(identifier(row['actualWorkItemId']) == identifier(row['originalPreparedWorkId'])
            == identifier(manifest['workItemId']) == identifier(row['spec']['editWorkItemId'])
            and row['publicManifestWorkItemId'] == row['sourcePreparedWorkItemIdExact'] == row['spec']['editWorkItemId']
            and row['publicManifestNewActionId'] == '',
            'Retain the exact public/native SOURCE identities; no NewAction or rewritten packet IDs.')
        package = read(record_pin(manifest['sourcePackage'],manifest['sourcePackageSha256'])['path'])
        require(manifest['sourcePackageSha256'] == row['packetSha256'] and package.get('composition') == row['spec'],
            'The sealed SOURCE packet bytes or full prepared DTO changed after the physical proof.')
        digest = numeric_payload(row['composition_export'],row['effective_values'],row['spec'])
        require(row['foreground_effective_channels_sha256'] == digest
            and row['receipt']['composition_export'] == row['composition_export']
            and row['receipt']['composition_metadata']['composition'] == row['spec'],
            'Published SOURCE effective digest or retained native receipt differs from the independent proof.')


def build(root=ROOT, *, first_stage, stage_config, stage_config_sha256,
          source_entry, source_entry_sha256, completion_closure, completion_closure_sha256,
          tools_receipt, tools_receipt_sha256, write=False):
    require(type(first_stage) is str and re.fullmatch('[a-z][a-z0-9_-]{0,63}',first_stage),
            'Select one explicit actual successful evidence directory name; no paths or latest fallback.')
    require(tools_receipt is not None and tools_receipt_sha256 is not None,
            'Actual generation requires the explicitly reviewed frozen tools receipt/path and digest.')
    root = Path(root).resolve()
    evidence, output = root / 'u1', root / 'u1_fixture.json'
    receipt_output = root / 'u1_fixture_source_receipt.json'
    require(not output.exists() and not receipt_output.exists(), 'Preserve existing UI fixture/receipt; never overwrite them.')
    empty_evidence(evidence)
    tools_pin, files, runner, native, checkpoints = frozen_api(root,
        receipt_path=tools_receipt,receipt_sha256=tools_receipt_sha256)
    pins = {tools_pin['path']: tools_pin['sha256'],
            **{str(Path(row['path']).resolve()):row['sha256'] for row in files.values()}}
    def record_pin(path, expected=None):
        value = pin(path, expected)
        pins[value['path']] = value['sha256']
        return value
    stage_directory = root / first_stage
    stage_config_pin = record_pin(stage_config,stage_config_sha256)
    source_pin = record_pin(source_entry,source_entry_sha256)
    closure_pin = record_pin(completion_closure,completion_closure_sha256)
    require(Path(stage_config_pin['path']) == stage_directory/'first_stage_config.json'
        and Path(source_pin['path']) == stage_directory/'source_entry_receipt.json'
        and Path(closure_pin['path']) == stage_directory/'completion_closure.json',
        'Config/source entry/completion closure must belong to the same explicitly selected actual attempt.')
    entry, closure = read(source_pin['path']),read(closure_pin['path'])
    require(entry.get('schema') == 'x.validation.character-composition-source-entry/1'
        and entry.get('config') == stage_config_pin,
        'The actual prepared source-entry receipt must bind this exact first-stage config.')
    audit = require_completion_closure(closure,first_stage,stage_config_pin,source_pin,record_pin)
    require(all(entry[field] == audit['immutable_source_proofs'][field] for field in SOURCE_PROOF_FIELDS),
        'Actual completion/source-entry immutable Parent/source/runtime proof pins differ.')
    stage_pin = record_pin(audit['reports']['first_stage']['path'],audit['reports']['first_stage']['sha256'])
    require(Path(stage_pin['path']) == stage_directory/'composition_first_stage_report.json',
        'The completion closure must pin this exact first-stage report.')
    stage = read(stage_pin['path'])
    require(stage.get('completed') is True, 'The selected stage has not completed; no first UI fixture was generated.')
    numeric_pin = record_pin(audit['reports']['numeric']['path'],audit['reports']['numeric']['sha256'])
    require(Path(numeric_pin['path']) == stage_directory/'composition_native_report.json',
        'The completion closure must pin this exact native report.')
    numeric = read(numeric_pin['path'])
    seed = numeric.get('qa_seed')
    require(type(seed) is dict and set(seed) == runner.SEED_FIELDS, 'No authenticated native QA seed receipt is available.')
    physical_pin = record_pin(seed['physicalReport'], seed['physicalReportSha256'])
    require(physical_pin == audit['reports']['two_a_physical'], 'Seed and completion closure pin different two-A physical bytes.')
    physical = read(physical_pin['path'])
    require_completed_stage(stage, numeric, physical)
    require(record_pin(stage['config'],stage['config_sha256']) == stage_config_pin,
        'The completed report no longer binds the explicitly supplied first-stage config.')
    stage_config = read(stage_config_pin['path'])
    require(stage_config.get('schema') == 'x.validation.character-composition-first-stage/1'
        and stage_config['driverSha256'] == files['run_character_composition_first_stage']['sha256'],
        'The actual completed first-stage driver differs from the approved frozen source.')
    for name,row in stage_config['tools'].items():
        require(name in files and record_pin(row['path'],row['sha256'])['sha256'] == files[name]['sha256'],
            'The actual completed first stage used another helper: ' + name)
    for name,row in entry['tools'].items():
        require(name in files and record_pin(row['path'],row['sha256'])['sha256'] == files[name]['sha256'],
            'Source-entry helper differs from approved frozen05: ' + name)
    fixture_pin = record_pin(numeric['fixture'], numeric['fixture_sha256'])
    require(stage_config['nativeFixture'] == fixture_pin, 'First-stage config/numeric fixture provenance differs.')
    original = native.validate_fixture(read(fixture_pin['path']))
    require(Path(original['evidenceDirectory']).resolve() == stage_directory, 'Use only the exact selected first-stage evidence.')
    require(record_pin(original['runtimeManifest'],original['runtimeManifestSha256']) == closure['python_projection'],
        'The successful native fixture and completion closure use different actual runtime bytes.')
    require(closure['artist']['sha256'] == original['inputBlendSha256'],
        'The completion closure and saved-artist input copy protect different original bytes.')
    projection_api = load(files['release_projection']['path'],'_first_ui_frozen_projection')
    for field,count in (('python_projection',167),('full_projection',173)):
        resolved = projection_api.load_source('character_designer',closure[field]['path'])
        require(len(resolved.files) == count and resolved.version == '0.77.25',
            'Require the actual complete frozen runtime/full file set and package version.')
        for name in resolved.files:
            record_pin(resolved.root/name,hashlib.sha256(resolved.files[name]).hexdigest())
    workspace_pin = record_pin(seed['workspacePath'], seed['workspaceSha256'])
    require(Path(original['compositionWorkspace']).resolve() == Path(workspace_pin['path'])
            and original['compositionWorkspaceSha256'] == workspace_pin['sha256'],
            'Parent changed the original workspace; first UI must precede Collect/Prepare/catalog mutation.')
    workspace = read(workspace_pin['path'])
    require(workspace['targetGuid'] == seed['targetGuid']
            and workspace['modelSha256'] == seed['modelSha256'] == original['compositionModelSha256'],
            'The current workspace must retain the native seed target/model identity.')
    record_pin(workspace['modelFile'], workspace['modelSha256'])
    specs = [read(record_pin(row['path'], row['sha256'])['path']) for row in original['specFiles']]
    native.validate_variant_specs(specs, original['bWorkItemIds'])
    require_numeric_cases(numeric,original,specs)
    selected = first_hold(specs, original, seed)
    for spec, packet_proof, publication in zip(specs, numeric['fresh_packet_proofs'], physical['items']):
        require(publication['spec'] == spec and identifier(publication['actualWorkItemId']) == identifier(spec['editWorkItemId']),
                'Physical publication must use this exact sealed prepared B variant.')
        matches = [row for row in workspace['clips'] if row.get('workItemId')
                   and identifier(row['workItemId']) == identifier(spec['editWorkItemId'])]
        require(len(matches) == 1 and matches[0]['composition'] == spec
                and matches[0]['editScope'] == 'UpperBodyOverride' and not matches[0]['needsRebuild']
                and not matches[0]['compositionReferenceSource'], 'Prepared B catalog identity changed.')
        clip = matches[0]
        require(Path(clip['linkManifest']).resolve() == Path(publication['manifestPath']).resolve(),
                'The prepared Link must equal the actually published SOURCE route.')
        package = read(record_pin(clip['sourcePackage'], clip['sourcePackageSha256'])['path'])
        require(package.get('composition') == spec and clip['sourcePackageSha256'] == packet_proof['packet_sha256'],
                'Prepared B packet composition/native packet proof changed.')
        record_pin(spec['referencePacketFile'], spec['referencePacketSha256'])
        record_pin(spec['weaponContextFile'], spec['weaponContextSha256'])
        record_pin(spec['weaponModelFile'], spec['weaponModelSha256'])
        companions = [row for row in workspace['clips'] if row.get('sourcePackage')
            and Path(row['sourcePackage']).resolve() == Path(spec['referencePacketFile']).resolve()
            and row.get('compositionOwnerLinkGuid') == clip['linkGuid']]
        require(len(companions) == 1 and companions[0]['compositionReferenceSource'] is True
                and companions[0]['composition'] is None and companions[0]['editScope'] == 'All'
                and companions[0]['sourcePackageSha256'] == spec['referencePacketSha256'],
                'Create A Work Copy requires the actual readonly A companion owned by this B Link.')
        companion_link = read(record_pin(companions[0]['linkManifest'])['path'])
        require(companion_link.get('compositionReferenceSource') is True
                and companion_link.get('compositionOwnerLinkGuid') == clip['linkGuid']
                and companion_link.get('composition') is None and companion_link.get('editScope') == 'All',
                'Readonly A companion Link provenance changed.')
    published_sources(physical, record_pin)
    published_sources({'items':[stage['loop_physical']]},record_pin,expected_count=1)
    loop_author_pin,loop_physical_pin = audit['reports']['loop_authoring'],audit['reports']['loop_physical']
    require(read(record_pin(loop_author_pin['path'],loop_author_pin['sha256'])['path']) == stage['loop_authoring']
        and read(record_pin(loop_physical_pin['path'],loop_physical_pin['sha256'])['path']) == stage['loop_physical'],
        'Third actual Loop standalone and nested complete receipts differ.')
    publications = physical['items'] + [stage['loop_physical']]
    require(len({identifier(row['actualWorkItemId']) for row in publications}) == 3,
        'The two prepared Holds and moving Loop must have three distinct genuine SOURCE identities.')
    for row,closed in zip(publications,audit['publications']):
        require(identifier(row['actualWorkItemId']) == identifier(closed['work_item_id'])
            and closed['kind'] == row['kind'] == 'SOURCE' and closed['revision'] == row['revision']
            and Path(closed['manifest_path']).resolve() == Path(row['manifestPath']).resolve()
            and all(closed[left] == row[right] for left,right in (
                ('manifest_sha256','manifest_after_sha256'),('fbx_sha256','fbx_sha256'),
                ('metadata_sha256','metadata_sha256')))
            and closed['effective_channels_sha256'] == row['composition_export']['effectiveChannelsSha256'],
            'Actual current SOURCE publication differs from the completed file audit.')
    audit_seed = audit['qa_seed']
    require(audit_seed['path'] == seed['seedBlend'] and audit_seed['sha256'] == seed['seedBlendSha256']
        and audit_seed['scene'] == seed['sceneName'] and audit_seed['rig_scene_names'] == seed['rigSceneNames']
        and audit_seed['native_item_ids'] == seed['itemIds']
        and Path(seed['seedBlend']).resolve() == Path(stage_config['qaSeedFile']).resolve()
        and Path(seed['seedBlend']).resolve().is_relative_to(stage_directory),
        'The actual completed audit and immutable QA seed native Scene/row/file identities differ.')
    for field, digest_field in (('inputBlend','inputBlendSha256'),('runtimeManifest','runtimeManifestSha256')):
        record_pin(original[field], original[digest_field])
    record_pin(seed['seedBlend'], seed['seedBlendSha256'])
    checks = {'createAWorkCopy': True, 'seekSeconds': [.137,.537], 'poseTolerance': 2e-4,
        'headfulCheckpoints': {'schema': checkpoints.SCHEMA, 'enabled': True,
            'helper': {key: files['composition_ui_checkpoints'][key] for key in ('path','sha256')},
            'settleSeconds': 2., 'timeoutSeconds': 1200.}}
    config = {'schema': runner.SCHEMA,
        **{key: original[key] for key in ('inputBlend','inputBlendSha256','addonPackage','runtimeManifest','runtimeManifestSha256')},
        'evidenceDirectory': str(evidence), 'numericReport': numeric_pin['path'], 'numericReportSha256': numeric_pin['sha256'],
        **{key: seed[key] for key in ('seedBlend','seedBlendSha256','sceneName','rigName','targetGuid','modelSha256')},
        'workItemId': selected,
        'sessionHelper': {key: files['composition_session_acceptance'][key] for key in ('path','sha256')},
        'sessionConfig': checks, 'quitOnCompletion': True}
    runner.validate_fixture(config)
    runner.validate_reports(config, numeric, physical, original)
    profiles = checkpoints.owned_profile(evidence, str(evidence/'CONFIG'), str(evidence/'DATAFILES'))
    profile_units = {path.name: len(str(path).encode('utf-16-le'))//2 for path in profiles}
    require(profile_units['DATAFILES'] + 174 < 260 and profile_units['CONFIG'] < 240,
            'The owned profile lost the strict Windows SOURCE-cache path budget.')
    require(all(not Path(row['path']).resolve().is_relative_to(evidence) for row in files.values()),
            'Frozen executable tools cannot live inside mutable UI evidence.')
    require(all(sha(path) == digest for path, digest in pins.items()), 'A receipt input changed during builder admission.')
    result = {'schema':'x.validation.character-composition-first-ui-builder/1', 'source_only':True,
        'native_launch_performed':False, 'first_stage':first_stage, 'frozen_tools_receipt':tools_pin, 'admitted_inputs':pins,
        'actual_stage_config':stage_config_pin,'actual_source_entry':source_pin,'completion_closure':closure_pin,
        'runner':{key:files['run_character_composition_ui_fixture'][key] for key in ('path','sha256')},
        'config_path':str(output), 'profiles':{path.name:str(path) for path in profiles},
        'profile_utf16_units':profile_units, 'generated':False,
        'gui_grant_consumed':False,
        'prerequisite':'A separate fresh Root-owned headful grant is required before launch and before Parent catalog mutation; source generation gives no GUI authorization. UI-edited seed never becomes Raise input.'}
    if write:
        # All read-only admission finishes before these new owned files/dirs.
        empty_evidence(evidence)
        with output.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(config,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
        evidence.mkdir(exist_ok=True)
        for profile in profiles:
            profile.mkdir(exist_ok=True)
        result.update(generated=True,fixture=pin(output))
        with receipt_output.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n')
    return config, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(ROOT),help='Actual validation evidence root.')
    parser.add_argument('--first-stage',required=True,help='Exact actual successful evidence directory name.')
    for name in ('stage-config','source-entry','completion-closure'):
        parser.add_argument('--'+name,required=True,help='Actual immutable absolute '+name+' path.')
        parser.add_argument('--'+name+'-sha256',required=True,help='Actual immutable full '+name+' SHA-256.')
    parser.add_argument('--tools-receipt',required=True,
        help='Root-approved frozen receipt absolute path; use the newly reviewed immutable headful snapshot.')
    parser.add_argument('--tools-receipt-sha256',required=True,
        help='Exact approved receipt SHA-256; no mutable canonical/latest fallback.')
    parser.add_argument('--write',action='store_true',help='Explicit root-only actual fixture generation after the selected stage succeeds.')
    args = parser.parse_args()
    _config, result = build(root=args.root,first_stage=args.first_stage,
        stage_config=args.stage_config,stage_config_sha256=args.stage_config_sha256,
        source_entry=args.source_entry,source_entry_sha256=args.source_entry_sha256,
        completion_closure=args.completion_closure,completion_closure_sha256=args.completion_closure_sha256,
        tools_receipt=args.tools_receipt,
        tools_receipt_sha256=args.tools_receipt_sha256,write=args.write)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))


if __name__ == '__main__':
    main()
