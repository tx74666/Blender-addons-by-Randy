"""Recover the n15 published SOURCE Loop without authoring, sampling or exporting.

This is an explicit isolated-QA entry. Source review does not authorize a native
launch. The archived selected Action is linked read-only; only its exact FK
curves enter the official editable draft. A fresh acknowledgement is recorded
only after complete curve, Custom, scope, clock and physical-publication proof.
The original failed reports and all three publications remain immutable.
"""
import argparse
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import traceback
import uuid

SCHEMA = 'x.validation.character-composition-publication-recovery-fixture/1'
REPORT_SCHEMA = 'x.validation.character-composition-publication-recovery/1'
FIELDS = {'schema', 'nativeFixture', 'failedSourceEntry', 'failedProtection',
    'loopAuthorReport', 'workerJob', 'workerSnapshot', 'workerResult',
    'publishedLink', 'inheritedPhysicalReport', 'inheritedNumericReport',
    'persistedNumericReport', 'sidecar_budget_projection_receipt', 'tools',
    'evidenceDirectory', 'qaSeedFile'}
NATIVE_SHA = '1ebf29f7ac4358884a3861164aa0e9894db37da57f74fe64a5ef3493b650ad32'
CONTINUATION_SHA = '23cda6830ce26421798df44b69b1c680b63536c393c9f22f16631f52818b411c'
RELEASE_SHA = '052e6c765c95ba46d480bf1eeca1e1dccb7685c77a4e756202ce409d20713106'
DRIVER_SHA = '701532d2fcc8024850811cd41e40c2b5dbd43e912ed95ab5f76824f6722521e6'
ARTIST_SHA = 'e8d3013ef14e02ed76160a838e7d255490fd1b6ece340e7c3f9e33dc57a3377f'
PROTECTION_SHA = '17125fc4cf1f1a4e23549b028f1c0c59155d9d7b8764dca2738fed76895422e1'
BEFORE_CUSTOM = '8f4aa620fc3c35afc47627cd82de14773ff1398a74484dba53076d94c063a11e'
AFTER_CUSTOM = '294db6aa173971d8ca472e9ffb498d521ee7f408395cf9ef68588ddcfa25f0c9'
INPUT_SHAS = {
    'failedSourceEntry': '111f933867cfd675b2204d250544518a21b507b106a1ec69020e85b1388bd534',
    'loopAuthorReport': 'dcbeccb0ec7185663dd6318627a9cf66e747c46c70f7231e5508fb7c7c3b3d4a',
    'workerJob': '8e36a2511052c4611943e9f660b713ada0361c62e276cb79c9c070b7353b6378',
    'workerSnapshot': 'e1598ff1838dd77c9ed6dfddee3702bdc0a76ce864975864cc745a8889fff0a6',
    'workerResult': '0f18fd875083d944eaf0acb104f677d9be8258137dce89451ea760aff2b7dd93',
    'publishedLink': '1c3b25a51cfdb7c1fbbc29be0e9877d1c68e5ddc9077af52856b4c23db4d3bea',
    'inheritedPhysicalReport': 'd1624f1b737b90c162a0fb1d0436102c59b20d20acf46b675fe9c675f179d5af',
    'inheritedNumericReport': 'c5d1170c43848b5d6ccb607953c7dad0cecbeb52291a81b2d490ead3020dfb6c',
    'persistedNumericReport': '32d5178e73f2900d21add8518ec6fd45fc4df40c41586f11a4abb00257d06eb9'}


def require(value, message):
    if not value:
        raise ValueError(message)


def checksum(value):
    require(type(value) is str and re.fullmatch('[0-9a-fA-F]{64}', value), 'Require a complete SHA-256.')
    return value.lower()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pin(row):
    require(type(row) is dict and set(row) == {'path', 'sha256', 'bytes'}
            and type(row['path']) is str and Path(row['path']).is_absolute()
            and type(row['bytes']) is int and row['bytes'] > 0, 'Require an exact absolute three-field file pin.')
    path = Path(row['path']).resolve()
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == row['bytes']
            and sha(path) == checksum(row['sha256']), 'Pinned file changed: ' + str(path))
    return path


def load(row, limit=32 * 1024 * 1024):
    path = pin(row)
    require(path.stat().st_size <= limit, 'Pinned JSON exceeds its explicit read bound.')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def receipt_pin(row):
    """Projection receipts add declared counts; preserve their actual file pin."""
    require(type(row) is dict and {'path', 'sha256', 'bytes'} <= set(row)
            and set(row) <= {'path', 'sha256', 'bytes', 'file_count', 'changed_product_files'},
            'Unexpected projection file-reference fields.')
    return {key: row[key] for key in ('path', 'sha256', 'bytes')}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_pin(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}


def equal_pin(a, b):
    return Path(a['path']).resolve() == Path(b['path']).resolve() and checksum(a['sha256']) == checksum(b['sha256'])


def helper(row, name):
    spec = importlib.util.spec_from_file_location(name, pin(row))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finite_values(values, sample_count):
    require(type(values) is dict and values.get('schema') == 'cdesigner.composition-effective-values/1'
            and type(values.get('timesSeconds')) is list and len(values['timesSeconds']) == sample_count
            and type(values.get('channels')) is list and len(values['channels']) == 410,
            'Require the complete published effective-values shape.')
    require(all(type(row) is list and len(row) == 2 and type(row[0]) is str and type(row[1]) is list
                and len(row[1]) == sample_count for row in values['channels'])
            and len({row[0] for row in values['channels']}) == 410, 'Missing or duplicated published channels.')
    numbers = values['timesSeconds'] + [value for row in values['channels'] for value in row[1]]
    require(all(type(value) in (int, float) and math.isfinite(value) for value in numbers)
            and all(a < b for a, b in zip(values['timesSeconds'], values['timesSeconds'][1:])),
            'All sample times and 410-channel values must be finite non-boolean numbers.')


def budget_projection(config, fixture):
    receipt = load(config['sidecar_budget_projection_receipt'])
    require(receipt.get('schema') == 'x.validation.character-composition-animation-sidecar-budget-projections/1'
            and receipt.get('source_only') is True and receipt.get('native_execution_performed') is False
            and receipt.get('release_or_deployment_performed') is False, 'Use the source-only receiver-budget projection receipt.')
    fix = receipt['fix']
    names = {'animation_link.py', 'animation_worklist_collection.py', 'animation_worklist_new.py'}
    require(set(fix['changed_runtime_sources']) == names, 'Only the three receiver-budget sources may change.')
    for row in fix['changed_runtime_sources'].values():
        pin(row)
    for row in fix.get('changed_embedded_sources', {}).values():
        pin(row)
    parent = load(fix['parent_range_fix_projection_receipt'])
    require(equal_pin(parent['python_projection'], receipt['previous_projections'][0])
            and equal_pin(parent['complete_candidate'], receipt['previous_projections'][1]), 'Receiver changes must follow the actual range-fixed projections.')
    previous, current = load(receipt['previous_projections'][0]), load(receipt_pin(receipt['python_projection']))
    require(previous['module'] == current['module'] == 'character_designer'
            and previous['version'] == current['version'] == '0.77.25'
            and len(previous['files']) == len(current['files']) == 167,
            'The complete .25 Python source inventory must remain 167 files.')
    old_files, new_files = previous['files'], current['files']
    changed = {name for name in old_files if old_files[name] != new_files.get(name)}
    require(set(old_files) == set(new_files) and changed == names, 'Numeric/export/scope/clock/worker sources may not change for inherited values.')
    require(Path(fixture['runtimeManifest']).resolve() == pin(receipt_pin(receipt['python_projection']))
            and checksum(fixture['runtimeManifestSha256']) == checksum(receipt['python_projection']['sha256'])
            and Path(fixture['addonPackage']).resolve() == Path(current['source_root']).resolve(),
            'The fresh native fixture must use exactly the receiver-budget Python projection.')
    for name, row in fix['changed_runtime_sources'].items():
        require(checksum(fix['before_sha256'][name]) == checksum(old_files[name])
                and checksum(row['sha256']) == checksum(new_files[name]), 'The receiver leaf transition differs from its manifest.')
        inverse = fix['independent_inverse_proof'][name]
        require(inverse['normalized_whole_source_inverse_exact'] is True
                and inverse['normalized_full_module_inverse_ast_equal'] is True
                and inverse['sampling_curves_hashes_slots_and_export_unchanged'] is True,
                'The receiver delta must preserve exact sampling/curves/slots/export semantics.')
    old_full, new_full = load(receipt['previous_projections'][1]), load(receipt_pin(receipt['complete_candidate']))
    embedded_names = set(fix['changed_embedded_sources'])
    return_name = 'unity_runtime/Editor/CharacterAnimationReturn.cs'
    transfer_name = 'unity_runtime/Editor/CharacterAnimationTransfer.cs'
    previous_shared_differences = {name for name, value in old_files.items() if old_full['files'][name] != value}
    current_shared_differences = {name for name, value in new_files.items() if new_full['files'][name] != value}
    require(embedded_names == {'unity_runtime/Editor/CharacterAnimationReturn.cs'}
            and len(old_full['files']) == len(new_full['files']) == 173
            and set(old_full['files']) == set(new_full['files'])
            and {name for name in old_full['files'] if old_full['files'][name] != new_full['files'][name]} == names | embedded_names
            and previous_shared_differences == current_shared_differences == {return_name, transfer_name}
            and new_files[return_name] == old_files[return_name] == receipt['runtime_historical_Return_sha256_retained']
            and old_full['files'][return_name] == fix['before_sha256'][return_name]
            and new_full['files'][return_name] == fix['changed_embedded_sources'][return_name]['sha256']
            and old_full['files'][transfer_name] == new_full['files'][transfer_name]
            and all(new_full['files'][name] == value for name, value in new_files.items() if name not in current_shared_differences),
            'The paired full projection may change only the same three Python receivers and the one actual C# reader.')
    require(fix['unchanged_exporter_source']['sha256'] == old_files['animation_composition_export.py']
            == new_files['animation_composition_export.py'], 'Inherited values require the exact range-fixed exporter.')
    pin(fix['unchanged_exporter_source'])
    for field in ('owner_source_report', 'independent_source_review', 'focused_pure_test_report'):
        pin(fix[field])
    return receipt


def admit(config, native, continuation):
    require(type(config) is dict and set(config) == FIELDS and config['schema'] == SCHEMA, 'Incomplete recovery fixture.')
    for field, expected in INPUT_SHAS.items():
        require(checksum(config[field]['sha256']) == expected, 'Use the exact actual n15/n11/n12 evidence: ' + field)
        pin(config[field])
    require(checksum(config['failedProtection']['sha256']) == PROTECTION_SHA, 'Pin the independently closed n15 natural failure.')
    protection, entry = load(config['failedProtection']), load(config['failedSourceEntry'])
    require(protection['schema'] == 'x.validation.character-composition-native-failure-protection/2'
            and protection['passed'] is True and protection['completed'] is False
            and protection['native_process_natural_exit_code'] == 1
            and protection['artist_file_unchanged'] is True
            and protection['official_collection_acceptance_completed'] is False
            and protection['full_outer_native_recovery_completed'] is False
            and protection['owned_qa_seed_created'] is False, 'Keep the real failed n15 state and its proof limits.')
    exit_record = load(protection['actual_process_exit'])
    require(exit_record['naturalExitCode'] == 1 and exit_record['forcedTermination'] is False
            and exit_record['allQaBlenderWorkersGoneVerified'] is True
            and len(exit_record['remainingBlenderProcesses']) == 1
            and exit_record['remainingBlenderProcesses'][0]['ProcessId'] == 27676,
            'The previous QA process and workers must have naturally exited; this does not replace fresh launch admission.')
    pin(protection['artist_file'])
    require(checksum(protection['artist_file']['sha256']) == ARTIST_SHA, 'The immutable artist file changed.')
    fixture = native.validate_fixture(load(config['nativeFixture']))
    old_config = load(entry['config']) if 'bytes' in entry['config'] else json.loads(Path(entry['config']['path']).read_text(encoding='utf-8'))
    require(sha(entry['config']['path']) == checksum(entry['config']['sha256']), 'The failed stage config changed.')
    old_fixture_path = Path(old_config['nativeFixture']['path'])
    require(sha(old_fixture_path) == checksum(old_config['nativeFixture']['sha256']), 'The failed native fixture changed.')
    old_fixture = json.loads(old_fixture_path.read_text(encoding='utf-8'))
    for field in set(old_fixture) - {'addonPackage', 'runtimeManifest', 'runtimeManifestSha256', 'evidenceDirectory'}:
        require(fixture[field] == old_fixture[field], 'The fresh recovery native input differs: ' + field)
    receipt = budget_projection(config, fixture)
    old, persisted = load(config['inheritedNumericReport']), load(config['persistedNumericReport'])
    original_fixture = json.loads(Path(old['fixture']).read_text(encoding='utf-8'))
    require(sha(old['fixture']) == checksum(old['fixture_sha256']), 'The original twelve-case fixture changed.')
    continuation.validate_numeric(old, original_fixture)
    require(persisted['completed'] is False and persisted['numeric_completed'] is True
            and persisted['cases'] == old['cases'] and persisted['effective_values'] == old['effective_values']
            and persisted['fresh_native_associations_verified'] is True
            and len(persisted['fresh_packet_proofs']) == len(persisted['fresh_associations']) == 2,
            'Keep actual n12 fresh association proof separate from the inherited twelve cases.')
    author, job, result, link = [load(config[field]) for field in ('loopAuthorReport', 'workerJob', 'workerResult', 'publishedLink')]
    partial = protection['actual_partial_third_source_publication']
    for field, actual in (('publishedLink', 'manifest'), ('workerJob', 'worker_job'), ('workerResult', 'worker_result')):
        require(equal_pin(config[field], partial[actual]), 'The partial publication evidence differs from n15 protection.')
    require(author['complete'] is True and author['pendingNativeOperator'] is False
            and author['all_other_Customs_Sources_unchanged'] is True and author['prior_state_restored'] is True
            and author['authoring']['before_sha256'] == BEFORE_CUSTOM
            and author['authoring']['after_sha256'] == AFTER_CUSTOM
            and author['authoring']['native_FK_transform_and_keys'] is True, 'Require the genuine completed native Loop authoring.')
    proof, private = author['numeric_proof'], job['composition_job']
    spec, values, public = private['composition'], private['effective_values'], private['composition_export']
    require(spec['timeMode'] == 'HoldLoop' and spec['editNewActionId'] == '' and len(spec['allowedBonePaths']) == 41
            and proof['completed'] is True and proof['authorization']['kind'] == 'SOURCE'
            and proof['authorization']['newActionId'] == '' and proof['spec'] == spec
            and proof['composition_export'] == public and proof['effective_values'] == values
            and link['composition'] == spec and link['revision'] == 1,
            'Archived author, worker and actual rev1 SOURCE Link must share the exact sealed DTO.')
    finite_values(values, 298)
    metadata = load(partial['metadata'])
    require(result['ok'] is True and result['source_curve_count'] == 405
            and type(result['bones']) is list and len(result['bones']) == len(set(result['bones'])) == 217
            and set(result['bones']) == set(private['scope']['mapping']['indices'])
            and result['samples'] == 298 and not result['unsupported_channels']
            and result['composition'] == metadata['composition'] == spec
            and result['composition_export'] == metadata['composition_export'] == public
            and result['effective_values'] == metadata['effective_values'] == values
            and digest(values) == public['effectiveChannelsSha256']
            and partial['worker_and_published_numeric_payload_equal'] is True,
            'All 410 by 298 published values must equal the real worker and foreground proof.')
    require(private['snapshot_sha256'] == checksum(config['workerSnapshot']['sha256'])
            and job['action_slot'] == 929201142 and job['fps'] == spec['fps'] == 60
            and job['fps_base'] == 1. and job['sample_rate'] == 60.
            and private['packet']['sampleRate'] == 60. and private['packet']['composition'] == spec,
            'The exact archived native clock, selected slot and snapshot must remain authenticated.')
    require(Path(link['fbxFile']).resolve() == pin(partial['fbx'])
            and checksum(link['fbxSha256']) == checksum(partial['fbx']['sha256'])
            and Path(link['metadataFile']).resolve() == pin(partial['metadata'])
            and checksum(link['metadataSha256']) == checksum(partial['metadata']['sha256']), 'Actual rev1 output hashes changed.')
    physical = load(config['inheritedPhysicalReport'])
    require(physical['completed'] is True and physical['physical_values_equal_foreground_and_two_a'] is True
            and len(physical['items']) == 2 and all(row['kind'] == 'SOURCE' and row['newActionId'] == ''
                and row['revision'] == 1 for row in physical['items']), 'The two real SOURCE publications must remain inherited.')
    publication_pins = protection['inherited_two_source_publications'] + [partial[name] for name in ('manifest', 'fbx', 'metadata')]
    for row in publication_pins:
        pin(row)
    overrides = {str(Path(row['path']).resolve()).casefold(): row for row in
        list(receipt['fix']['changed_runtime_sources'].values()) + list(receipt['fix'].get('changed_embedded_sources', {}).values())
        + list(receipt['fix'].get('changed_tool_sources', {}).values()) + publication_pins}
    for row in protection['source_input_checks']:
        path = str(Path(row['path']).resolve())
        expected = overrides.get(path.casefold(), {'path': path, 'sha256': row['actual_sha256'], 'bytes': row['bytes']})
        pin(expected)
    evidence = Path(config['evidenceDirectory']).resolve()
    require(evidence.is_absolute() and evidence == Path(fixture['evidenceDirectory']).resolve()
            and evidence.is_dir() and Path(config['qaSeedFile']).resolve().parent == evidence
            and not Path(config['qaSeedFile']).exists(), 'Use a fresh, separately owned recovery evidence tree and seed.')
    return fixture, {'protection': protection, 'entry': entry, 'budget': receipt, 'old': old,
        'persisted': persisted, 'author': author, 'job': job, 'result': result, 'link': link,
        'physical': physical, 'publication_pins': publication_pins}


def native_inventory(bpy):
    return {name: sorted(block.as_pointer() for block in getattr(bpy.data, name)) for name in ('objects', 'scenes', 'armatures', 'meshes', 'materials')}


def curve_rows(action, slot, backend, fp, *, channels=None):
    bag = backend._bag(action, slot)
    curves = list(bag.fcurves)
    keys = [(curve.data_path, curve.array_index) for curve in curves]
    require(len(keys) == len(set(keys)), 'Recovery cannot rewrite duplicate curves.')
    if channels is not None:
        require(set(keys) == set(channels) and len(keys) == 405, 'Recovery must contain exactly the complete 405 selected FK curves.')
    for curve in curves:
        require(curve.group is None and not curve.modifiers and not curve.sampled_points
                and getattr(curve, 'driver', None) is None and not getattr(curve, 'is_driver', False),
                'Groups, drivers, modifiers and sampled curves are outside this exact transfer contract.')
    payload = [backend._curve_payload(curve) for curve in sorted(curves, key=lambda curve: (curve.data_path, curve.array_index))]
    fingerprints = [fp._curve_payload(curve) for curve in sorted(curves, key=lambda curve: (curve.data_path, curve.array_index))]
    canonical(payload)
    canonical(fingerprints)
    return payload, fingerprints


def anchor_record(anchor):
    return {'row': anchor['row'], 'spec': anchor['spec'],
        'custom_action': anchor['custom_action'].name,
        'custom_native_pointer': anchor['custom_action'].as_pointer(),
        'custom_slot': anchor['custom_slot'], 'fingerprint': anchor['fingerprint']}


def restore_curves(action, slot, rows, backend):
    bag = backend._bag(action, slot)
    for curve in list(bag.fcurves):
        bag.fcurves.remove(curve)
    for row in rows:
        backend._write_curve(bag, row)


def transfer(context, rig, item_id, scope, facts, config, modules, report):
    """One native draft transaction; no new keys/transforms/samples or workers."""
    import bpy
    native, worklist, fp, backend, export, ua = [modules[name] for name in ('native', 'worklist', 'fp', 'backend', 'export', 'ua')]
    item = native.reacquire_work_item(context, rig, item_id, worklist)
    custom, handle = item.custom_action, item.custom_slot
    source_before = backend._motion_hash(item.source_action, item.source_slot)
    require(backend._motion_hash(custom, handle) == BEFORE_CUSTOM, 'The fresh full Custom does not equal genuine authoring before-state.')
    original, _ = curve_rows(custom, handle, backend, fp)
    outside = {(curve.data_path, curve.array_index): backend._curve_payload(curve)
        for curve in backend._curves(custom, handle) if (curve.data_path, curve.array_index) not in set(scope.channels)}
    archive, session = None, None
    committed = False
    source_inventory = native_inventory(bpy)
    original_action_pointers = {action.as_pointer() for action in bpy.data.actions}
    had_drafts = backend.DRAFTS in rig
    prior_drafts = dict(rig.get(backend.DRAFTS, {}))
    draft, draft_owner = None, None
    try:
        snapshot = pin(config['workerSnapshot'])
        with bpy.data.libraries.load(str(snapshot), link=True) as (available, selected):
            require(list(available.actions).count(facts['job']['action']) == 1, 'The exact archived isolated Action is missing or ambiguous.')
            selected.actions = [facts['job']['action']]
        require(len(selected.actions) == 1 and selected.actions[0] is not None, 'Link exactly one archived Action.')
        archive = selected.actions[0]
        require(native_inventory(bpy) == source_inventory, 'Linking the Action imported unowned scene/object/data dependencies.')
        require({action.as_pointer() for action in bpy.data.actions} == original_action_pointers | {archive.as_pointer()},
                'Linking the archive must add exactly its one Action and no other Action dependencies.')
        archive_handle = facts['job']['action_slot']
        require(archive.as_pointer() not in original_action_pointers
                and archive.library is not None and Path(archive.library.filepath).resolve() == snapshot
                and archive.is_action_layered and len(archive.slots) == 1 and len(archive.layers) == 1
                and len(archive.layers[0].strips) == 1 and archive.layers[0].strips[0].type == 'KEYFRAME'
                and len(archive.layers[0].strips[0].channelbags) == 1
                and fp._slot(archive, archive_handle).target_id_type == 'OBJECT'
                and ua.native_action_frame_rate(archive) == facts['job']['fps'],
                'Archived Action structure, library, exact slot or authenticated native clock changed.')
        rows, old_curve_fingerprints = curve_rows(archive, archive_handle, backend, fp, channels=scope.channels)
        archive_fp = export.effective_fingerprint(archive, archive_handle, scope, facts['job']['composition_job']['composition'])
        require(archive_fp['fingerprint_known'] is True, 'The archive cannot prove all selected curves and timing.')
        backend.enter(context, facts['job']['composition_job']['composition'], item_id, packet=backend._packet_for_item(item))
        session, current_item, current_scope, _tracks = backend.validate(context, rig)
        require(current_item.item_id == item_id and current_item.custom_action is custom
                and current_item.custom_slot == handle and session['edit_target'] == 'B'
                and canonical(export._worker_scope(current_scope)) == canonical(facts['job']['composition_job']['scope']),
                'The official native draft does not have the exact worker scope.')
        reference_before = backend._motion_hash(session['base_action'], session['base_slot'])
        require(reference_before == facts['job']['composition_job']['composition_export']['referenceActionBeforeSha256'],
                'The protected actual A does not equal the archived reference proof.')
        draft, draft_slot = session['edit_action'], session['edit_slot']
        draft_owner = session['owner']
        require(draft.as_pointer() not in original_action_pointers
                and draft.as_pointer() != archive.as_pointer()
                and draft.get(backend.OWNER) == draft_owner
                and {action.as_pointer() for action in bpy.data.actions} ==
                    original_action_pointers | {archive.as_pointer(), draft.as_pointer()},
                'Enter may add only its authenticated new B Edit draft, with no additional Action dependencies.')
        require(set((curve.data_path, curve.array_index) for curve in backend._curves(draft, draft_slot)) == set(scope.channels),
                'The official draft contains missing/outside channels before transfer.')
        restore_curves(draft, draft_slot, rows, backend)
        copied_rows, copied_fp = curve_rows(draft, draft_slot, backend, fp, channels=scope.channels)
        require(canonical(copied_rows) == canonical(rows) and canonical(copied_fp) == canonical(old_curve_fingerprints)
                and export.effective_fingerprint(draft, draft_slot, current_scope, session['spec']) == archive_fp,
                'Native key/handle/settings transfer changed the archived selected curves.')
        require(backend.commit(context, rig) is True, 'The genuine scoped Custom commit did not complete.')
        committed = True
        session, item, current_scope, _tracks = backend.validate(context, rig)
        selected_custom = [curve for curve in backend._curves(custom, handle) if (curve.data_path, curve.array_index) in set(scope.channels)]
        selected_rows = [backend._curve_payload(curve) for curve in sorted(selected_custom, key=lambda curve: (curve.data_path, curve.array_index))]
        selected_fp = [fp._curve_payload(curve) for curve in sorted(selected_custom, key=lambda curve: (curve.data_path, curve.array_index))]
        require(canonical(selected_rows) == canonical(rows) and canonical(selected_fp) == canonical(old_curve_fingerprints)
                and backend._motion_hash(custom, handle) == AFTER_CUSTOM
                and export.effective_fingerprint(custom, handle, current_scope, session['spec']) == archive_fp
                and outside == {(curve.data_path, curve.array_index): backend._curve_payload(curve)
                    for curve in backend._curves(custom, handle) if (curve.data_path, curve.array_index) not in set(scope.channels)}
                and backend._motion_hash(item.source_action, item.source_slot) == source_before
                and backend._motion_hash(session['base_action'], session['base_slot']) == reference_before,
                'Recovered full Custom/outside curves/protected Source/A differ from the actual authored archive.')
        report.update(archive_action={'path': str(snapshot), 'sha256': config['workerSnapshot']['sha256'],
            'name': archive.name, 'slot': archive_handle, 'library': archive.library.filepath},
            current_item_id=item_id, current_custom_action=custom.name, current_custom_slot=handle,
            complete_worker_scope_equal=True, worker_scope_sha256=digest(export._worker_scope(current_scope)),
            structural_proof_sha256=digest(current_scope.proof), selected_curve_count=405,
            curve_payload_sha256=digest(rows), selected_native_curve_payload_sha256=digest(old_curve_fingerprints),
            fresh_scoped_fingerprint=archive_fp, archive_scoped_fingerprint=archive_fp,
            full_custom_before_sha256=BEFORE_CUSTOM, full_custom_after_sha256=AFTER_CUSTOM,
            recovered_selected_curves_exact=True, protected_outside_curves_unchanged=True,
            protected_Source_unchanged=True, protected_A_unchanged=True,
            archived_public_source_slot=archive_handle, actual_current_native_slot=handle,
            native_clock_verified=ua.native_action_frame_rate(custom), transform_or_key_authoring_performed=False,
            samples_reconstructed=False, numeric_resampling_performed=False)
        backend.exit(context, rig, writeback=True)
        require(not backend.active(rig) and backend._motion_hash(custom, handle) == AFTER_CUSTOM,
                'Official Close must retain the recovered Custom and leave no active session.')
        report['inner_composition_closed'] = True
    except BaseException:
        rollback_errors = []
        if backend.active(rig):
            try:
                backend.exit(context, rig, writeback=False)
            except BaseException as exc:
                rollback_errors.append('Close: ' + str(exc))
        try:
            restore_curves(custom, handle, original, backend)
            require(backend._motion_hash(custom, handle) == BEFORE_CUSTOM, 'Rollback full Custom differs.')
        except BaseException as exc:
            rollback_errors.append('Custom rollback: ' + str(exc))
        report.update(transfer_failed=True, transfer_committed_before_failure=committed,
                      transfer_rollback_errors=rollback_errors, transfer_rollback_verified=not rollback_errors)
        raise
    finally:
        primary_error = sys.exc_info()[0] is not None
        try:
            if draft is not None:
                require(not backend.active(rig) and draft.get(backend.OWNER) == draft_owner,
                        'Do not dispose a live or foreign native draft after recovery.')
                expected_drafts = {**prior_drafts, draft_owner: draft}
                actual_drafts = dict(rig.get(backend.DRAFTS, {}))
                require(set(actual_drafts) == set(expected_drafts)
                        and all(actual_drafts[key].as_pointer() == value.as_pointer()
                            for key, value in expected_drafts.items()),
                        'The persisted draft table contains changes beyond the exact owned recovery draft.')
                if had_drafts:
                    rig[backend.DRAFTS] = prior_drafts
                else:
                    rig.pop(backend.DRAFTS, None)
                draft.use_fake_user = False
                require(draft.users == 0, 'The recovered temporary Edit draft still has an unexpected native user.')
                bpy.data.actions.remove(draft)
                report['temporary_owned_edit_draft_disposed'] = True
            if archive is not None and archive.as_pointer() not in original_action_pointers:
                require(archive.users == 0, 'The linked recovery archive still has an unexpected native user.')
                bpy.data.actions.remove(archive)
            require(native_inventory(bpy) == source_inventory, 'Archive transfer changed global object/Scene/data inventories.')
            require({action.as_pointer() for action in bpy.data.actions} == original_action_pointers,
                    'Recovery cleanup must restore the exact original global Action inventory.')
            report['global_Action_inventory_restored'] = True
        except BaseException as exc:
            report.setdefault('archive_cleanup_errors', []).append(str(exc))
            if not primary_error:
                raise


def acknowledge(context, rig, item_id, facts, modules, report):
    """A new receipt event; never reconstruct an absent original launch proof."""
    native, worklist, backend, collector = [modules[name] for name in ('native', 'worklist', 'backend', 'collector')]
    item = native.reacquire_work_item(context, rig, item_id, worklist)
    require(not backend.active(rig) and backend._motion_hash(item.custom_action, item.custom_slot) == AFTER_CUSTOM,
            'Acknowledge only after exact recovered full Custom and official Close.')
    proof, implementation = collector.item_fingerprint(context, item), collector.export_implementation()
    require(proof['fingerprint_known'] is True
            and proof['authored_b_sha256'] == report['fresh_scoped_fingerprint']['authored_b_sha256']
            and proof['effective_input_sha256'] == report['fresh_scoped_fingerprint']['effective_input_sha256'],
            'The fresh acknowledgement B fingerprint differs from the recovered archive.')
    metadata = proof['composition_metadata']
    require(metadata['composition'] == facts['link']['composition']
            and metadata['reference_action_sha256'] == facts['job']['composition_job']['composition_export']['referenceActionBeforeSha256'],
            'The fresh metadata and protected actual A differ from the real publication.')
    link = facts['link']
    event = {'_worklist_scene': context.scene, '_worklist_item_id': item_id,
        '_worklist_fingerprint': proof, '_worklist_export_implementation': implementation,
        '_worklist_composition_metadata': metadata, '_worklist_composition': metadata['composition'],
        '_worklist_composition_export': facts['job']['composition_job']['composition_export']}
    result = {'link_manifest': item.manifest_path, 'revision': link['revision'],
        'fbx_sha256': link['fbxSha256'], 'filepath': link['fbxFile'], 'unsupported_channels': []}
    error = collector.export_finished(event, result=result)
    report['collection_receipt_error'] = error
    require(not error, error or 'The official collector rejected the fresh acknowledgement.')
    current = native.reacquire_work_item(context, rig, item_id, worklist)
    receipt = json.loads(current.last_synced_receipt)
    require(receipt['proof'] == proof and receipt['implementation'] == implementation
            and receipt['composition_metadata'] == metadata
            and receipt['composition_export'] == event['_worklist_composition_export']
            and receipt['publication'] == collector._publication(link), 'The fresh official receipt differs from the accepted publication.')
    report.update(completed=True, kind='SOURCE', actualWorkItemId=link['workItemId'], newActionId='', revision=1,
        publicationInherited=True, source_export_executed=False, fresh_receipt_acceptance_count=1,
        archived_launch_fingerprint_reconstructed=False, fresh_receipt_proof=proof,
        fresh_receipt_implementation=implementation, current_last_synced_receipt=receipt,
        existing_publication_result_wrapper=result, result_wrapper_is_new_recovery_event=True,
        physical_numeric_values_inherited=True, effective_channel_count=410, sample_count=298,
        spec=facts['job']['composition_job']['composition'],
        composition_export=facts['job']['composition_job']['composition_export'],
        effective_values=facts['job']['composition_job']['effective_values'])


def run(config, report):
    native_row, continuation_row = [config['tools'][name] for name in
        ('verify_character_composition_native', 'run_character_composition_first_stage_continuation')]
    require(set(config['tools']) == {'verify_character_composition_native', 'run_character_composition_first_stage_continuation'}
            and checksum(native_row['sha256']) == NATIVE_SHA and checksum(continuation_row['sha256']) == CONTINUATION_SHA,
            'Keep the existing native seam and inherited numeric reader unchanged.')
    native = helper(native_row, '_publication_recovery_native')
    continuation = helper(continuation_row, '_publication_recovery_original_continuation')
    driver_row = {'path': str(pin(native_row).parent / 'run_character_composition_first_stage.py'),
        'sha256': DRIVER_SHA, 'bytes': 9852}
    driver = helper(driver_row, '_publication_recovery_resource_driver')
    release = pin({'path': str(pin(native_row).parent / 'release_projection.py'), 'sha256': RELEASE_SHA, 'bytes': 7529})
    require('release_projection' not in sys.modules
            or Path(sys.modules['release_projection'].__file__).resolve() == release,
            'A cached release helper from another toolset cannot admit this recovery.')
    fixture, facts = admit(config, native, continuation)
    evidence = Path(config['evidenceDirectory']).resolve()
    report_file, numeric_file = evidence / 'publication_recovery_report.json', evidence / 'publication_recovery_native_report.json'
    require(not report_file.exists() and not numeric_file.exists(), 'Recovery outputs must be new and exclusive.')
    report.update(native_launch_permission_inferred=False, numeric_cases_inherited=12, numeric_cases_reexecuted=0,
        fresh_isolated_variants_inherited=2, fresh_isolated_variants_executed=0,
        physical_source_publications_inherited=3, physical_source_publications_executed=0,
        current_receipt_acceptances=0, current_loop_imports=0, current_UI_imports_executed=0,
        native_authoring_executed=0, numeric_resamples_executed=0,
        worker_exports_executed=0, inherited_first_stage_completed=False,
        loop_authoring=facts['author'], two_a_physical=facts['physical'],
        inherited_inputs={key: config[key] for key in INPUT_SHAS}, sidecar_budget_projection_receipt=config['sidecar_budget_projection_receipt'])
    import bpy
    numeric = {'schema': native.REPORT_SCHEMA, 'completed': False, 'fixture': str(pin(config['nativeFixture'])),
        'fixture_sha256': config['nativeFixture']['sha256']}
    report['numeric'] = numeric
    def cases_runner(cfg, numeric, workspace_path, rig, scene, preservation, prior_actions, prior_assets, specs, _callback):
        names = ('animation_worklist', 'animation_link', 'animation_link_source', 'animation_worklist_fingerprint',
                 'animation_composition', 'animation_composition_export', 'unity_animation', 'animation_worklist_target', 'animation_worklist_collection')
        values = [importlib.import_module('character_designer.' + name) for name in names]
        worklist, links, source, fp, backend, export, ua, target, collector = values
        modules = dict(zip(('worklist', 'links', 'source', 'fp', 'backend', 'export', 'ua', 'target', 'collector'), values))
        modules['native'] = native
        saved = worklist.state(bpy.context)
        original_workspace = (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256,
            rig.get('character_designer_animation_source_model_sha256'))
        original_association = {key: rig[key] for key in (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
        original_session = target.capture_session_metadata(rig)
        backend.connect_library(bpy.context, str(workspace_path))
        loop_spec = facts['job']['composition_job']['composition']
        requested_specs = [loop_spec] + specs
        ids, anchors = [], []
        for spec in requested_specs:
            routes = [row for row in worklist.state(bpy.context).composition_catalog if row.ready
                and uuid.UUID(row.work_item_id) == uuid.UUID(spec['editWorkItemId'])]
            require(len(routes) == 1, 'The exact current genuine SOURCE import route is missing or ambiguous.')
            key = str(routes[0].clip_key)
            del routes
            driver.check_memory(report, 'before_official_import_' + spec['editWorkItemId'])
            item = backend.add_work_item(bpy.context, key, activate_new=False)
            item_id = str(item.item_id)
            del item
            ids.append(item_id)
            anchors.append(native.capture_import_anchor(bpy.context, rig, item_id, worklist, fp))
            for prior_id, anchor in zip(ids, anchors):
                native.reacquire_work_item(bpy.context, rig, prior_id, worklist, anchor=anchor, fingerprint=fp)
        require(len(ids) == len(set(ids)) == 3, 'Three unique actual SOURCE rows are required.')
        report['current_loop_imports'] = 1
        loop_id, loop_anchor = ids[0], anchors[0]
        captured, previous = worklist._capture_rig(bpy.context, rig), source._context_state(bpy.context)
        previous_index = saved.active_index
        loop_report = {'completed': False}
        report['loop_physical'] = loop_report
        loop_report['import_anchor_before_transfer'] = anchor_record(loop_anchor)
        try:
            worklist.activate(bpy.context, loop_id, 'CUSTOM')
            loop_report['fresh_association'] = native.fresh_association_proof(bpy.context, rig, loop_id, links, worklist,
                cfg['compositionModelSha256'], anchor=loop_anchor, fingerprint=fp)
            item = native.reacquire_work_item(bpy.context, rig, loop_id, worklist, anchor=loop_anchor, fingerprint=fp)
            packet = backend._packet_for_item(item)
            scope = export.resolve_scope(bpy.context, rig, loop_spec, packet)
            require(backend.item_spec(item) == loop_spec and packet['_sha256'] == facts['job']['composition_job']['packet']['_sha256']
                    and canonical(packet) == canonical(facts['job']['composition_job']['packet'])
                    and len(scope.mapping.indices) == 217 and math.isfinite(scope.mapping.error_metres)
                    and 0 <= scope.mapping.error_metres <= .0002
                    and canonical(export._worker_scope(scope)) == canonical(facts['job']['composition_job']['scope']),
                    'The current exact native217 Rest/packet/Scope differs from the real worker domain.')
            transfer(bpy.context, rig, loop_id, scope, facts, config, modules, loop_report)
            loop_report['current_recovered_anchor'] = anchor_record(
                native.capture_import_anchor(bpy.context, rig, loop_id, worklist, fp))
            acknowledge(bpy.context, rig, loop_id, facts, modules, loop_report)
            report['current_receipt_acceptances'] = 1
        finally:
            worklist._restore_rig(bpy.context, rig, captured)
            source._restore_context(bpy.context, previous, playing=True)
            saved.active_index = previous_index
            bpy.context.view_layer.update()
        require(loop_report['completed'] is True and loop_report['inner_composition_closed'] is True,
                'Actual Loop recovery and official receipt must finish before the current UI seed.')
        numeric.update(cases=json.loads(json.dumps(facts['old']['cases'])), effective_values=facts['old']['effective_values'],
            numeric_completed=True, fresh_packet_proofs=facts['persisted']['fresh_packet_proofs'],
            fresh_associations=facts['persisted']['fresh_associations'], worker_launch_performed=False,
            same_b_across_two_a_seek_and_object_motion=facts['old']['same_b_across_two_a_seek_and_object_motion'],
            numeric_reuse={'schema': 'x.validation.character-composition-publication-recovery-numeric-reuse/1',
                'source': config['inheritedNumericReport'], 'old_report_completed': False, 'cases_reexecuted': False,
                'persisted_fresh_native_source': config['persistedNumericReport'], 'fresh_isolated_variants_inherited': 2,
                'fresh_isolated_variants_executed': 0, 'sidecar_budget_projection_receipt': config['sidecar_budget_projection_receipt'],
                'inherited_protected_numeric_flags': {name: facts['old'][name] for name in
                    ('protected_prior_actions_unchanged', 'protected_assets_unchanged', 'historical_recovery_association_unchanged')}})
        for index, (item_id, anchor, spec) in enumerate(zip(ids[1:], anchors[1:], specs)):
            item = native.reacquire_work_item(bpy.context, rig, item_id, worklist, anchor=anchor, fingerprint=fp)
            packet = backend._packet_for_item(item)
            scope = export.resolve_scope(bpy.context, rig, spec, packet)
            current = {'work_item_id': spec['editWorkItemId'], 'model_sha256': item.model_sha256,
                'packet_sha256': packet['_sha256'], 'source_rest_sha256': spec['sourceRestSha256'],
                'original_rest_sha256': scope.proof['sourceRest'], 'motion_rest_sha256': scope.proof['motionRest'],
                'native_count': len(scope.mapping.indices), 'mapping_error_metres': scope.mapping.error_metres}
            inherited = facts['persisted']['fresh_packet_proofs'][index]
            b_proof = backend.item_fingerprint(bpy.context, item)
            case = next(row for row in facts['old']['cases'] if row['reference'] == 'a' + str(index + 1))
            require(backend.item_spec(item) == spec and current['native_count'] == 217
                    and {key: value for key, value in current.items() if key != 'mapping_error_metres'} ==
                        {key: value for key, value in inherited.items() if key != 'mapping_error_metres'}
                    and math.isfinite(current['mapping_error_metres']) and 0 <= current['mapping_error_metres'] <= .0002
                    and b_proof == case['b_fingerprint'], 'Current UI row differs from its actual inherited packet/Rest/B proof.')
            captured, previous = worklist._capture_rig(bpy.context, rig), source._context_state(bpy.context)
            previous_index = saved.active_index
            try:
                worklist.activate(bpy.context, item_id, 'CUSTOM')
                association = native.fresh_association_proof(bpy.context, rig, item_id, links, worklist,
                    cfg['compositionModelSha256'], anchor=anchor, fingerprint=fp)
                require(association == facts['persisted']['fresh_associations'][index], 'Current UI association differs from the protected native source route.')
            finally:
                worklist._restore_rig(bpy.context, rig, captured)
                source._restore_context(bpy.context, previous, playing=True)
                saved.active_index = previous_index
                bpy.context.view_layer.update()
            numeric.setdefault('currentImportedForUI', []).append({'item_id': item_id, 'packet_rest_proof': current,
                'association': association, 'b_fingerprint': b_proof, 'numeric_resampled': False,
                'isolated_sync_executed': False, 'source_export_executed': False,
                'import_anchor': anchor_record(anchor)})
        require(backend._motion_hash(native.reacquire_work_item(bpy.context, rig, loop_id, worklist).custom_action,
                native.reacquire_work_item(bpy.context, rig, loop_id, worklist).custom_slot) == AFTER_CUSTOM,
                'Current UI imports changed the recovered full Loop Custom.')
        native.assert_actions(prior_actions, fp)
        require(not preservation.protected_preview_changes(prior_assets, preservation.asset_snapshot(scene, rig, ua)),
                'Fresh recovery changed protected raw display/assets.')
        for row in facts['publication_pins']:
            pin(row)
        numeric.update(current_ui_native_associations_verified=True, fresh_native_associations_verified=True)
        report['current_UI_imports_executed'] = 2
        driver.check_memory(report, 'before_save_current_recovered_QA_seed')
        report['qa_seed'] = native.save_qa_seed(bpy.context, numeric, config['qaSeedFile'],
            physical_report_path=str(pin(config['inheritedPhysicalReport'])))
        require(original_workspace == (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256,
                    rig.get('character_designer_animation_source_model_sha256'))
                and original_association == {key: rig[key] for key in (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
                and original_session == target.capture_session_metadata(rig), 'Fresh QA recovery association/session must be restored before outer Close.')
        numeric['fresh_inner_recovery_verified'] = True
        numeric['protected_prior_actions_unchanged'] = True
        numeric['protected_assets_unchanged'] = True
    try:
        driver.check_memory(report, 'before_fresh_native_adoption_and_recovery')
        native.run(fixture, numeric, cases_runner=cases_runner)
        require(numeric['completed'] is True and numeric['fresh_workspace_cleanup'] == 'historical_native_state_and_rows_restored',
                'Fresh original artist scope and protected rows/assets must actually restore.')
        for row in facts['publication_pins']:
            pin(row)
        for name in INPUT_SHAS:
            pin(config[name])
        report.update(completed=True, full_outer_native_recovery_completed=True,
            original_scene_recovery_verified=True, artist_save_performed=False,
            existing_publications_byte_unchanged=True, source_packets_rewritten=False)
        numeric['historical_recovery_association_unchanged'] = True
    finally:
        with numeric_file.open('x', encoding='utf-8') as stream:
            json.dump(numeric, stream, ensure_ascii=False, allow_nan=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--config-sha256', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else None)
    config_path = Path(args.config).resolve()
    require(sha(config_path) == checksum(args.config_sha256), 'The recovery config changed.')
    config = json.loads(config_path.read_text(encoding='utf-8'))
    report = {'schema': REPORT_SCHEMA, 'completed': False, 'fixture': file_pin(config_path),
        'native_execution_performed': True, 'source_review_implies_native_acceptance': False}
    destination = Path(config['evidenceDirectory']).resolve() / 'publication_recovery_report.json'
    require(destination.parent.is_dir() and not destination.exists(), 'The report destination must be fresh and owned.')
    try:
        run(config, report)
    except BaseException as exc:
        report.update(error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)


if __name__ == '__main__':
    main()
