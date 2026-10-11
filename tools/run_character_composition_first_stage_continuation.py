"""Explicit continuation of pinned n11 numerics and optional n12 physical tail.

Run only in a separately authorized disposable Blender process. Import does not
open a file, launch a worker, read resources, write evidence or issue a grant.
The old failed report stays immutable; fresh ownership and export are re-proved.
"""
import argparse
import ast
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import traceback
import uuid

OLD_NUMERIC_SHA = 'c5d1170c43848b5d6ccb607953c7dad0cecbeb52291a81b2d490ead3020dfb6c'
FAILURE_READBACK_SHA = '2291e1e7360b8431eafb0d2fd86b12eafe486d96a2760b58051867468a0a6189'
OLD_NATIVE_SHA = 'ce4be885e95ef720f617712ca1bacc0347872a2a1542ade6cf8f9e86d6d34632'
DRIVER_SHA = '701532d2fcc8024850811cd41e40c2b5dbd43e912ed95ab5f76824f6722521e6'
ADMISSION_FIELDS = {'schema', 'numeric_report', 'failure_readback', 'owner_review', 'peer_review', 'projection_receipt'}
PHYSICAL_TAIL_FIELDS = {'schema', 'first_stage_report', 'native_report', 'physical_report', 'loop_failure',
                        'failure_readback', 'source_entry', 'owner_review', 'peer_review'}
SHORT_TAIL_FIELDS = {'latest_failure_readback', 'latest_exit', 'latest_source_entry'}
SHORT_TAIL_PINS = {
    'latest_failure_readback': '829c2758f1e7b33e52ed46571891a55d2dc31313a9816fc9cb951f607e459488',
    'latest_exit': '04a8fb7185d7080169bbb8708ff6f5838269d319a6f30a9492a38e29baf14bc2',
    'latest_source_entry': 'f2b38a3aca7639618396c00db45c8d9a6e25020bc3d71321a55feea1a51ad1cc'}
LATEST_CHANGED_SOURCES = {
    'run_character_composition_first_stage_continuation.py': 'af08e593e350646a0055cb689641631c08e111015a61219b713a45a14d441435',
    'composition_timing_acceptance.py': 'a89ffd1a3acf4937ef033bf4dfad1c77bc34ec883bc032f7dc0cf44e1bb99874',
    'build_character_composition_continuation_fixture.py': 'ec25dc21856d27d7b785fa58c2b78e8e8009ad96d68381c17b325a14d206f112'}
PHYSICAL_TAIL_PINS = {'first_stage_report': '916860cd83d4c2eafbc346017659855d96ba42f1f3bcaeea2fb182e365c4467d',
    'native_report': '32d5178e73f2900d21add8518ec6fd45fc4df40c41586f11a4abb00257d06eb9',
    'physical_report': 'd1624f1b737b90c162a0fb1d0436102c59b20d20acf46b675fe9c675f179d5af',
    'loop_failure': '67d00b7fe4529e60eeacd7aff58f40d7a268414b92674ff7b58b80e7e9f50a43',
    'failure_readback': '041253e90a8baae9ed715c28b4fde0e002aeea3e57ff595b41911e08011f5ff7',
    'source_entry': '845394a52e7ba286c20fe100f1bbd69a559ae7771678b3589b906bb3acec31ec'}
TAIL_CHANGED_SOURCES = {
    r'D:\MyRepository\Blender-addons-by-Randy\tools\run_character_composition_first_stage_continuation.py':
        '0da0072321607a2e5e31974a8bc4889d04fcb670430b8cebf0dd7443e231f2f9',
    r'D:\MyRepository\Blender-addons-by-Randy\tools\composition_timing_acceptance.py':
        '58325c8b4256585a12370aa10a059eabb1d80c74fe3d2eb4b2fbb97fecf0cb06',
    r'D:\MyRepository\Blender-addons-by-Randy\tools\build_character_composition_continuation_fixture.py':
        '239a2b187106dc5592da74fc2bae12c5e37df87356ffc233cd50f4646ecef339'}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pin(row):
    require(type(row) is dict and {'path', 'sha256'} <= set(row)
            and Path(row['path']).is_absolute() and type(row['sha256']) is str
            and len(row['sha256']) == 64, 'Use an explicit absolute path and full SHA-256.')
    path = Path(row['path']).resolve()
    require(path.is_file() and sha(path) == row['sha256'].lower(), 'Pinned input changed: ' + str(path))
    if 'bytes' in row:
        require(type(row['bytes']) is int and path.stat().st_size == row['bytes'], 'Pinned byte count changed.')
    return path


def load(row):
    value = json.loads(pin(row).read_text(encoding='utf-8'))
    json.dumps(value, allow_nan=False)
    return value


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def functions(path):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    funcs = {node.name: ast.dump(node, include_attributes=False) for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    top = [ast.dump(node, include_attributes=False) for node in tree.body
           if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return funcs, top


def range_projection(admission, fixture=None):
    """Validate the separate guard-only step; retain serializer facts as history."""
    row = admission.get('range_fix_projection_receipt')
    if row is None:
        return None
    require(row['sha256'].lower() == '001311420fbe4f255ed18b4fe5fb69680f2f7048d0cbe9eaee8068f1f68e34bc',
            'Use the exact separately reviewed native frame-precision projection.')
    receipt = load(row)
    require(receipt['schema'] == 'x.validation.character-composition-native-frame-precision-projections/1'
            and receipt['source_only'] is True and receipt['native_execution_performed'] is False
            and receipt['release_or_deployment_performed'] is False
            and receipt['embedded_overlay_count'] == 8 and receipt['embedded_overlays_unchanged'] is True,
            'Native frame coverage admission gives no retime, Unity change or execution authority.')
    fix, name = receipt['fix'], 'animation_composition_export.py'
    serializer = load(admission['projection_receipt'])
    require(fix['parent_serializer_projection_receipt'] == admission['projection_receipt']
            and [{key: row[key] for key in ('path', 'sha256', 'bytes')} for row in receipt['previous_projections']] ==
                [{key: row[key] for key in ('path', 'sha256', 'bytes')} for row in (serializer['python_projection'], serializer['complete_candidate'])]
            and fix['changed_functions'] == {name: ['_validate_action_timing']}
            and fix['added_functions'] == {name: []}
            and set(fix['changed_runtime_sources']) == {name}
            and fix['before_sha256'] == {name: '2dd2173ec604918328b25945f22916d96ce8039a322c7ed034ee65f686b69984'},
            'The range step must follow the exact original serializer projection and change only its timing guard.')
    require(Path(fix['superseded_source']['path']).resolve() ==
                Path(receipt['previous_projections'][0]['path']).parent / 'character_designer' / name
            and fix['superseded_source']['sha256'] == fix['before_sha256'][name]
            and fix['superseded_source']['bytes'] == 47158,
            'Superseded canonical bytes must be proved by the immutable runtime11 exporter.')
    old_path = pin(fix['superseded_source']); new_path = pin(fix['changed_runtime_sources'][name])
    require(Path(fix['superseded_canonical_source_path']).resolve() == new_path
            and fix['changed_runtime_sources'][name]['sha256'] ==
                '562565879a9c4c05356f82c3b0ec45bd567d1311ef58cbde8a20607452079520', 'Unreviewed canonical exporter replacement.')
    owner, peer = [load(fix[key]) for key in ('owner_source_report', 'independent_source_review')]
    require(owner['passed'] is True and peer['passed'] is True
            and owner['nativeExecutionPerformed'] is False and peer['nativeExecutionPerformed'] is False
            and owner['changedFunctions'] == peer['independentComparison']['changedFunctions'] == ['_validate_action_timing']
            and owner['sourceInverseExact'] is True
            and peer['independentComparison']['inverseNormalizedSourceTextExact'] is True
            and peer['independentComparison']['wholeModuleAstExactExceptTimingFunction'] is True,
            'Require both actual Parent SOURCE-only timing-guard reviews.')
    for prior, current, count in zip(receipt['previous_projections'],
            (receipt['python_projection'], receipt['complete_candidate']), (167, 173)):
        before, after = load(prior), load(current)
        require(set(before['files']) == set(after['files']) and len(after['files']) == count
                and [key for key in before['files'] if before['files'][key] != after['files'][key]] == [name]
                and before['files'][name] == fix['before_sha256'][name]
                and after['files'][name] == fix['changed_runtime_sources'][name]['sha256'],
                'Only the single timing-admission exporter leaf may differ in either complete projection.')
    replacement = """    # Blender stores FCurve key coordinates as float32. Compare coverage in
    # that native precision; the sealed seconds and export sampling stay exact.
    try:
        requested_first, requested_last = (
            struct.unpack('<f', struct.pack('<f', frame))[0]
            for frame in _frame_range(spec))
    except (OverflowError, struct.error) as error:
        raise ValueError('B sample bounds exceed finite native frame coordinates.') from error
    _require(math.isfinite(requested_first) and math.isfinite(requested_last),
             'B sample bounds exceed finite native frame coordinates.')"""
    text = new_path.read_text(encoding='utf-8')
    require(text.count(replacement) == 1, 'Require exactly the native float32 coverage block; never alter source seconds.')
    inverse = text.replace(replacement, '    requested_first, requested_last = _frame_range(spec)')
    require(ast.dump(ast.parse(inverse), include_attributes=False) ==
            ast.dump(ast.parse(old_path.read_text(encoding='utf-8')), include_attributes=False),
            'Curve/matrix/sampling/hash/isolation/worker or any other exporter code changed.')
    native_leaf = Path(receipt['python_projection']['path']).parent / 'character_designer' / name
    require(sha(native_leaf) == fix['changed_runtime_sources'][name]['sha256'], 'Current frozen exporter differs.')
    if fixture is not None:
        require(Path(fixture['runtimeManifest']).resolve() == pin(receipt['python_projection'])
                and fixture['runtimeManifestSha256'] == receipt['python_projection']['sha256'], 'Fixture must bind the actual range projection.')
    return receipt


def historical_pin(row, range_fix=None):
    """Only the exact superseded canonical exporter uses an immutable old leaf."""
    if range_fix is not None:
        fix = range_fix['fix']
        if (Path(row['path']).resolve() == Path(fix['superseded_canonical_source_path']).resolve()
                and row['sha256'].lower() == fix['superseded_source']['sha256']):
            return pin(fix['superseded_source'])
    return pin(row)


def validate_delta(old, fixture, admission):
    range_fix = range_projection(admission, fixture)
    runtime_fixture = dict(fixture)
    if range_fix is not None:
        prior = range_fix['previous_projections'][0]
        runtime_fixture.update(runtimeManifest=prior['path'], runtimeManifestSha256=prior['sha256'],
                               addonPackage=str(Path(prior['path']).parent / 'character_designer'))
    reviews = [load(admission[key]) for key in ('owner_review', 'peer_review')]
    require(all(row.get('source_only') is True and row.get('native_execution_performed') is False
                and row.get('passed') is True for row in reviews), 'Require completed SOURCE-only owner and peer review.')
    facts = reviews[0]['numeric_reuse_delta']
    require(facts == reviews[1]['numeric_reuse_delta'] and facts['serialization_only'] is True
            and facts['changed_functions'] == {'animation_composition_export.py': ['begin_isolated_export']}
            and set(facts['added_functions']) == {'animation_composition_export.py'}
            and set(facts['added_functions']['animation_composition_export.py']) == {'_snapshot_editor_state', '_prepare_snapshot_editor'}
            and len(facts['added_functions']['animation_composition_export.py']) == 2,
            'Both reviewers must identify the same serialization-only function delta.')
    require(Path(pin(facts['old_projection'])) == Path(old['runtime']['manifest']).resolve()
            and facts['old_projection']['sha256'] == old['runtime']['manifest_sha256'], 'Old projection differs from n11.')
    before = load(facts['old_projection'])
    after = load({'path': runtime_fixture['runtimeManifest'], 'sha256': runtime_fixture['runtimeManifestSha256']})
    require(set(before['files']) == set(after['files']) and len(before['files']) == 167,
            'Reuse requires the exact complete 167-file native product.')
    changed = [name for name in before['files'] if before['files'][name] != after['files'][name]]
    require(changed == ['animation_composition_export.py'], 'A shared evaluator or another product source changed.')
    old_path = Path(old['runtime']['package']) / changed[0]
    new_path = Path(runtime_fixture['addonPackage']) / changed[0]
    require(sha(old_path) == before['files'][changed[0]] and sha(new_path) == after['files'][changed[0]], 'Exporter source pin changed.')
    require(reviews[0]['changed_runtime_sources'] == reviews[1]['changed_runtime_sources']
            and set(reviews[0]['changed_runtime_sources']) == set(changed), 'Review source map differs.')
    reviewed = reviews[0]['changed_runtime_sources'][changed[0]]
    require(sha(historical_pin(reviewed, range_fix)) == after['files'][changed[0]], 'Historical serializer projection does not contain its reviewed exporter.')
    old_funcs, old_top = functions(old_path)
    new_funcs, new_top = functions(new_path)
    added = set(facts['added_functions'][changed[0]])
    require(old_top == new_top and set(new_funcs) == set(old_funcs) | added
            and not added & set(old_funcs) and len(added) == len(facts['added_functions'][changed[0]])
            and all(old_funcs[name] == new_funcs[name] for name in old_funcs if name != 'begin_isolated_export'),
            'Shared isolation, curves, matrices, seeds, time or numeric functions changed.')
    tree = ast.parse(new_path.read_text(encoding='utf-8'))
    begin = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'begin_isolated_export')
    wanted = ast.dump(ast.parse('_prepare_snapshot_editor(rig, original)').body[0], include_attributes=False)
    parents = [node for node in ast.walk(begin) if isinstance(node, ast.Try)]
    matches = [(node.body, index) for node in parents for index, child in enumerate(node.body)
               if ast.dump(child, include_attributes=False) == wanted]
    require(len(matches) == 1, 'Only the exact snapshot-boundary call may be added.')
    body, index = matches[0]
    require(index + 1 < len(body) and isinstance(body[index + 1], ast.Expr)
            and isinstance(body[index + 1].value, ast.Call)
            and ast.unparse(body[index + 1].value.func) == 'bpy.data.libraries.write', 'Cleanup must immediately precede snapshot serialization.')
    del body[index]
    require(ast.dump(begin, include_attributes=False) == old_funcs['begin_isolated_export'], 'Snapshot launch changed beyond one serialization call.')
    return facts


def validate_numeric(old, old_fixture):
    require(old.get('schema') == 'x.validation.character-composition-native/1'
            and old.get('completed') is False and old.get('numeric_completed') is True
            and old.get('worker_launch_performed') is False
            and old.get('fresh_workspace_cleanup') == 'historical_native_state_and_rows_restored', 'Use the immutable failed n11 numeric report.')
    flags = ('same_b_across_two_a_seek_and_object_motion', 'protected_prior_actions_unchanged',
             'protected_assets_unchanged', 'fresh_native_associations_verified', 'historical_recovery_association_unchanged')
    require(all(old.get(key) is True for key in flags), 'The old complete numerical/protection proof is required.')
    require(all(old[key]['full_scope_verified'] is True and old[key]['native_count'] == 217
                for key in ('qa_fk_preflight', 'qa_fk_post_adoption')), 'Original pre/post full217 admission is missing.')
    cases = old['cases']
    expected = {(label, obj, time) for label in ('a1', 'a2') for obj in range(2) for time in old_fixture['seekSeconds']}
    require(len(cases) == 12 and len(expected) == 12
            and {(row['reference'], row['object_motion_case'], row['seek_seconds']) for row in cases} == expected,
            'Require all twelve exact two-A/two-object/three-seek cases.')
    values, base = old['effective_values'], cases[0]['b_fingerprint']
    require(values['schema'] == 'cdesigner.composition-effective-values/1'
            and len(values['channels']) == 410 and len(values['timesSeconds']) == 4
            and all(a < b for a, b in zip(values['timesSeconds'], values['timesSeconds'][1:]))
            and len({row[0] for row in values['channels']}) == 410
            and all(len(row[1]) == len(values['timesSeconds']) for row in values['channels']), 'Incomplete numeric samples.')
    numbers = values['timesSeconds'] + [value for row in values['channels'] for value in row[1]]
    require(all(type(value) in (int, float) and math.isfinite(value) for value in numbers), 'Require all 410 by 4 samples and times to be finite numbers, excluding booleans.')
    for index, label in enumerate(('a1', 'a2')):
        rows = [row for row in cases if row['reference'] == label]
        require(all(row['native_count'] == 217 and row['b_fingerprint'] == base and base['fingerprint_known'] is True
                    and uuid.UUID(row['b_work_item_id']) == uuid.UUID(old_fixture['bWorkItemIds'][index])
                    and row['composition_export']['effectiveChannelsSha256'] == digest(values)
                    and row['composition_export']['referenceActionBeforeSha256'] == row['composition_export']['referenceActionAfterSha256']
                    and row['composition_export']['excludedReferenceEvaluation'] is True for row in rows), 'Old B/A numerical proof differs.')
    require(len({row['visible_c_sha256'] for row in cases}) > 1
            and len({row['composition_export']['referenceActionBeforeSha256'] for row in cases}) == 2,
            'The original test did not exercise two different genuine A evaluations.')


def physical_tail(config, admission, old, fixture):
    """Admit only the two actual n12 publications and reviewed source changes."""
    tail = admission['physical_tail']
    range_fix = range_projection(admission, fixture)
    short = type(tail) is dict and tail.get('schema') == 'x.validation.character-composition-physical-tail-reuse/2'
    require(type(tail) is dict and set(tail) == (PHYSICAL_TAIL_FIELDS | SHORT_TAIL_FIELDS if short else PHYSICAL_TAIL_FIELDS)
            and tail['schema'] == ('x.validation.character-composition-physical-tail-reuse/2' if short else
                                  'x.validation.character-composition-physical-tail-reuse/1')
            and all(tail[key]['sha256'] == value for key, value in PHYSICAL_TAIL_PINS.items()),
            'Use the exact failed n12 tail and completed two-SOURCE evidence.')
    data = {key: load(tail[key]) for key in PHYSICAL_TAIL_PINS}
    first, numeric, physical, loop, failure, entry = [data[key] for key in
        ('first_stage_report', 'native_report', 'physical_report', 'loop_failure', 'failure_readback', 'source_entry')]
    source_inputs = {Path(path).resolve(): value for path, value in entry['source_inputs'].items()}
    require(first['completed'] is False and first['continuation'] is True and first['numeric'] == numeric
            and first['two_a_physical'] == physical and 'loop_physical' not in first and 'qa_seed' not in first
            and numeric['completed'] is False and numeric['numeric_completed'] is True
            and numeric['fresh_workspace_cleanup'] == 'historical_native_state_and_rows_restored'
            and numeric['cases'] == old['cases'] and numeric['effective_values'] == old['effective_values']
            and loop['complete'] is False and loop['failure_phase'] == 'author_actual_HoldLoop_Custom'
            and first['error'] == 'The actual native HoldLoop middle FK Pose rotation did not finish.',
            'Do not relabel either failed attempt or admit another tail failure.')
    require(failure['schema'] == 'x.validation.character-composition-natural-failure-readback/1'
            and failure['source_only'] is True and failure['passed'] is True and failure['attempt'] == 'n12'
            and failure['native_run_completed'] is False and failure['full_acceptance_passed'] is False
            and failure['source_input_count'] == 903 and failure['source_inputs_unchanged'] == 901
            and failure['actual_two_pose_exports_complete'] is True and failure['loop_authoring_complete'] is False
            and failure['loop_failure'] == loop and failure['qa_seed_created'] is False
            and failure['third_source_published'] is False
            and failure['natural_exit_record']['naturalExitCode'] == 1
            and failure['natural_exit_record']['forcedTermination'] is False
            and failure['natural_exit_record']['allQaBlenderWorkersGoneVerified'] is True,
            'The actual failed n12 publication/exit readback is incomplete.')
    for row in failure['files']:
        pin(row)
    reviews = [load(tail[key]) for key in ('owner_review', 'peer_review')]
    require(all(row.get('source_only') is True and row.get('native_execution_performed') is False
                and row.get('passed') is True for row in reviews), 'Require pinned SOURCE-only tail owner and peer.')
    changes = reviews[0]['reviewed_source_changes']
    require(changes == reviews[1]['reviewed_source_changes']
            and {Path(path).resolve() for path in changes} == {Path(path).resolve() for path in TAIL_CHANGED_SOURCES},
            'Only the exact reviewed continuation, timing and fixture producer sources may change.')
    normalized_changes = {Path(path).resolve(): value for path, value in changes.items()}
    overrides = {}
    for path, value in changes.items():
        expected = next(before for name, before in TAIL_CHANGED_SOURCES.items() if Path(name).resolve() == Path(path).resolve())
        require(set(value) == {'before_sha256', 'after_sha256'} and value['before_sha256'] == expected
                and value['after_sha256'] != expected, 'Reviewed source replacement differs from its old immutable pin.')
        overrides[Path(path).resolve()] = {'path': path, 'sha256': value['after_sha256']}
        pin(overrides[Path(path).resolve()])
    if range_fix is not None:
        overrides[Path(range_fix['fix']['superseded_canonical_source_path']).resolve()] = range_fix['fix']['changed_runtime_sources']['animation_composition_export.py']
    entry_source = next(row for path, row in overrides.items() if path.name == Path(__file__).name)
    timing_source = next(row for path, row in overrides.items() if path.name == 'composition_timing_acceptance.py')
    require(sha(__file__) == entry_source['sha256']
            and config['tools']['composition_timing_acceptance']['sha256'] == timing_source['sha256']
            and config['tools']['verify_character_composition_native']['sha256'] == '1ebf29f7ac4358884a3861164aa0e9894db37da57f74fe64a5ef3493b650ad32',
            'Actual frozen tail helpers differ from the reviewed changes or unchanged native seam.')
    require(physical['schema'] == 'x.validation.character-composition-sync/1' and physical['completed'] is True
            and physical['physical_values_equal_foreground_and_two_a'] is True
            and physical['actual_fbx_and_metadata_hashes_verified'] is True and len(physical['items']) == 2,
            'Require both completed genuine n12 SOURCE publications.')
    published, inherited_files = {}, []
    for index, row in enumerate(physical['items']):
        baseline = next(case for case in old['cases'] if case['reference'] == 'a' + str(index + 1))
        require(row['completed'] is True and row['kind'] == 'SOURCE' and row['newActionId'] == '' and row['revision'] == 1
                and uuid.UUID(row['actualWorkItemId']) == uuid.UUID(fixture['bWorkItemIds'][index])
                and row['actual_fbx_and_metadata_hashes_verified'] is True
                and row['physical_values_equal_independent_foreground'] is True
                and row['effective_values'] == old['effective_values']
                and row['composition_export'] == baseline['composition_export']
                and digest(row['effective_values']) == row['composition_export']['effectiveChannelsSha256'],
                'Inherited SOURCE kind, native identities or complete independent numeric proof differs.')
        manifest_pin = {'path': row['manifestPath'], 'sha256': row['manifest_after_sha256']}
        manifest = load(manifest_pin)
        require(manifest['revision'] == row['revision'] == 1 and manifest['workItemId'] == row['spec']['editWorkItemId']
                and manifest['composition'] == row['spec'] and manifest['sourcePackageSha256'] == row['packetSha256']
                and row['publicManifestWorkItemId'] == row['sourcePreparedWorkItemIdExact'] == manifest['workItemId']
                and row['publicManifestNewActionId'] == '' and row['spec']['editNewActionId'] == '',
                'The actual revision1 public SOURCE Link identity changed.')
        package = load({'path': manifest['sourcePackage'], 'sha256': manifest['sourcePackageSha256']})
        require(package['composition'] == row['spec'], 'The sealed B source packet differs from its inherited publication.')
        inherited_files.append(manifest_pin)
        for path_key, hash_key, link_path, link_hash in (
                ('fbx_file', 'fbx_sha256', 'fbxFile', 'fbxSha256'),
                ('metadata_file', 'metadata_sha256', 'metadataFile', 'metadataSha256')):
            record = {'path': row[path_key], 'sha256': row[hash_key]}
            require(Path(manifest[link_path]).resolve() == pin(record) and manifest[link_hash] == row[hash_key],
                    'The actual inherited FBX/metadata bytes or Link output route changed.')
            inherited_files.append(record)
        metadata = load({'path': row['metadata_file'], 'sha256': row['metadata_sha256']})
        require(metadata['composition'] == row['spec'] and metadata['composition_export'] == row['composition_export']
                and metadata['effective_values'] == row['effective_values'], 'Inherited metadata proof changed.')
        path = Path(row['manifestPath']).resolve()
        require(path not in published and source_inputs[path] == row['manifestSha256'],
                'The inherited pre-publication manifest identity is unavailable or duplicated.')
        published[path] = manifest_pin
    changed_links = {Path(row['path']).resolve(): row for row in failure['authorized_source_manifest_changes']}
    require(set(changed_links) == set(published) and len(changed_links) == 2
            and all(changed_links[path]['sha256'] == record['sha256']
                    and changed_links[path]['expected_sha256'] == source_inputs[path]
                    and changed_links[path]['matches_expected'] is False for path, record in published.items()),
            'Only the two exact authorized post-publication Link hashes may replace historical hashes.')
    overrides.update(published)
    source_rows = {Path(row['path']).resolve(): row for row in failure['source_rehash']}
    require(len(source_rows) == len(failure['source_rehash']) == len(source_inputs) == len(entry['source_inputs']) == 903
            and set(source_rows) == set(source_inputs),
            'The original 903-source admission or readback is incomplete.')
    current_source_pins = []
    for path, row in source_rows.items():
        expected = source_inputs[path]
        require(row['expected_sha256'] == expected
                and (row['sha256'] == published[path]['sha256'] and row['matches_expected'] is False if path in published
                     else row['sha256'] == expected and row['matches_expected'] is True), 'Original source readback differs.')
        if path in overrides and path not in published:
            if path in normalized_changes:
                require(expected == normalized_changes[path]['before_sha256'], 'Reviewed source is not from the actual old903 set.')
            else:
                require(range_fix is not None and expected == range_fix['fix']['superseded_source']['sha256'], 'Unreviewed product override.')
        current = overrides.get(path, {'path': str(path), 'sha256': expected})
        pin(current)
        current_source_pins.append(current)
    if short:
        require(all(tail[key]['sha256'] == value for key, value in SHORT_TAIL_PINS.items()),
                'Use the exact consumed n14 range rejection, exit and immutable source admission.')
        latest, actual_exit, latest_entry = [load(tail[key]) for key in
            ('latest_failure_readback', 'latest_exit', 'latest_source_entry')]
        require(latest['schema'] == 'x.validation.character-composition-native-failure-protection/1'
                and latest['attempt'] == 'n14' and latest['completed'] is False
                and latest['source_input_count'] == 964 and latest['source_inputs_all_unchanged'] is True
                and latest['full_outer_native_recovery_completed'] is False
                and latest['historical_native_data_rest_FK_action_slot_rows_session_association_restored'] is True
                and latest['loop_authoring_complete'] is False and latest['loop_export_performed'] is False
                and latest['owned_qa_seed_created'] is False and latest['current_UI_imports_executed'] == 0
                and latest['native_process_natural_exit_code'] == 1 and latest['native_rotate_finished_count'] == 1
                and latest['native_keyframe_insert_count'] == 3 and latest['loop_authoring_native_keys_and_commit_proven'] is True
                and latest['actual_failure_phase'] == 'measure_genuine_foreground_B_for_physical_Sync'
                and latest['actual_error'] == 'Moving B sample bounds exceed its prepared duration or authored selected-slot FK range.'
                and latest['automatic_retry_or_forced_termination_performed'] is False
                and latest['actual_process_exit_values'] == actual_exit
                and actual_exit['attempt'] == 'n14' and actual_exit['naturalExitCode'] == 1
                and actual_exit['forcedTermination'] is False and actual_exit['allQaBlenderWorkersGoneVerified'] is True
                and Path(latest['actual_process_exit']['path']).resolve() == pin(tail['latest_exit'])
                and latest['actual_process_exit']['sha256'] == tail['latest_exit']['sha256']
                and Path(latest['source_entry']['path']).resolve() == pin(tail['latest_source_entry'])
                and latest['source_entry']['sha256'] == tail['latest_source_entry']['sha256']
                and len(latest['actual_absent_success_paths']) == 5
                and all(not Path(path).exists() for path in latest['actual_absent_success_paths']),
                'The failed n14 real keys and boundary rejection cannot authorize a completed Loop or seed.')
        for row in latest['files']:
            pin(row)
        latest_inputs = {Path(path).resolve(): value for path, value in latest_entry['source_inputs'].items()}
        checks = {Path(row['path']).resolve(): row for row in latest['source_input_checks']}
        require(len(checks) == len(latest['source_input_checks']) == len(latest_inputs) == 964
                and set(checks) == set(latest_inputs), 'The actual n14 964-source readback is incomplete.')
        for path, expected in latest_inputs.items():
            require(checks[path]['expected_sha256'] == checks[path]['actual_sha256'] == expected,
                    'The consumed n14 source baseline changed.')
            if path in normalized_changes:
                require(expected == LATEST_CHANGED_SOURCES[path.name], 'A reviewed tool does not match its latest source baseline.')
            if path in overrides and path not in normalized_changes and path not in published:
                require(range_fix is not None and expected == range_fix['fix']['superseded_source']['sha256'], 'Unreviewed latest exporter replacement.')
            current = overrides[path] if path in overrides else {'path': str(path), 'sha256': expected}
            pin(current)
            current_source_pins.append(current)
        retained = {Path(row['path']).resolve(): row for row in latest['inherited_two_source_publications']}
        require(latest['inherited_two_source_publications_unchanged'] is True
                and latest['inherited_two_source_revisions'] == [1, 1]
                and len(retained) == 6 and set(retained) == {Path(row['path']).resolve() for row in inherited_files}
                and all(retained[Path(row['path']).resolve()]['sha256'] == row['sha256'] for row in inherited_files),
                'The latest failure must preserve all six exact rev1 SOURCE publication files.')
        for row in retained.values():
            pin(row)
        require(numeric['runtime']['manifest_sha256'] == (range_fix['previous_projections'][0]['sha256'] if range_fix is not None else fixture['runtimeManifestSha256'])
                and numeric['runtime']['files'] == 167 and len(numeric['fresh_packet_proofs']) == len(numeric['fresh_associations']) == 2
                and numeric['fresh_associations'] == old['fresh_associations']
                and all(numeric.get(key) is True for key in ('numeric_completed', 'same_b_across_two_a_seek_and_object_motion',
                    'fresh_native_associations_verified', 'protected_prior_actions_unchanged', 'protected_assets_unchanged',
                    'historical_recovery_association_unchanged'))
                and numeric['numeric_reuse']['cases_reexecuted'] is False
                and numeric['numeric_reuse']['fresh_isolated_variants_verified'] == 2,
                'Only the persisted actual n12 full-scope two-variant proof may be inherited.')
        for index, value in enumerate(numeric['fresh_packet_proofs']):
            require({key: data for key, data in value.items() if key != 'mapping_error_metres'} ==
                    {key: data for key, data in old['fresh_packet_proofs'][index].items() if key != 'mapping_error_metres'}
                    and value['native_count'] == 217 and math.isfinite(value['mapping_error_metres'])
                    and 0 <= value['mapping_error_metres'] <= .0002, 'The persisted n12 native217/Rest/packet proof differs.')
    return {'physical': physical, 'source_overrides': overrides, 'inherited_files': inherited_files,
            'admission': tail, 'physical_report': tail['physical_report'], 'current_source_pins': current_source_pins,
            'before_source_hashes': source_inputs, 'short': short, 'persisted_fresh_numeric': numeric}


def admit(config, admission, native):
    require(type(admission) is dict and set(admission) in (ADMISSION_FIELDS, ADMISSION_FIELDS | {'physical_tail'},
                ADMISSION_FIELDS | {'physical_tail', 'range_fix_projection_receipt'})
            and admission['schema'] == 'x.validation.character-composition-numeric-reuse/1'
            and admission['numeric_report']['sha256'] == OLD_NUMERIC_SHA
            and admission['failure_readback']['sha256'] == FAILURE_READBACK_SHA, 'No arbitrary numeric report may authorize continuation.')
    range_fix = range_projection(admission)
    old, failure = load(admission['numeric_report']), load(admission['failure_readback'])
    require(failure['evidence_readback_passed'] is True and failure['native_run_completed'] is False
            and failure['full_acceptance_passed'] is False and len(failure['source_rehash']) == 28, 'Use actual failed-worker source readback.')
    files = {Path(row['path']).resolve(): row for row in failure['files']}
    for row in files.values():
        pin(row)
    old_fixture = native.validate_fixture(load({'path': old['fixture'], 'sha256': old['fixture_sha256']}))
    fixture = native.validate_fixture(load(config['nativeFixture']))
    ignored = {'addonPackage', 'runtimeManifest', 'runtimeManifestSha256', 'evidenceDirectory', 'specFiles'}
    require({k: v for k, v in fixture.items() if k not in ignored} == {k: v for k, v in old_fixture.items() if k not in ignored}
            and [row['sha256'] for row in fixture['specFiles']] == [row['sha256'] for row in old_fixture['specFiles']], 'Actual model/A/B/spec/time/object inputs changed.')
    tail = physical_tail(config, admission, old, fixture) if 'physical_tail' in admission else None
    old_entry = next(load(row) for path, row in files.items() if path.name == 'source_entry_receipt.json')
    for row in failure['source_rehash']:
        require(row['matches_expected'] is True and row['sha256'] == old_entry['source_inputs'][row['path']], 'Source input is not admitted by n11.')
        if tail and Path(row['path']).resolve() in tail['source_overrides']:
            require(row['sha256'] == tail['before_source_hashes'][Path(row['path']).resolve()],
                    'Historical source override is not the exact n12 pre-publication/source baseline.')
        pin(tail['source_overrides'].get(Path(row['path']).resolve(), row) if tail else row)
    result = next(load(row) for path, row in files.items() if path.name == 'result.json')
    require(result['ok'] is False and result['error'] == 'Snapshot contains unowned objects; A evaluation was not excluded.', 'The prior failure differs.')
    validate_numeric(old, old_fixture)
    facts = validate_delta(old, fixture, admission)
    projection = load(admission['projection_receipt'])
    require(projection['schema'] == 'x.validation.character-composition-snapshot-serializer-projections/1'
            and projection['source_only'] is True and projection['native_execution_performed'] is False
            and projection['release_or_deployment_performed'] is False and projection['embedded_overlay_count'] == 8
            and projection['embedded_overlays_unchanged'] is True
            and projection['fix']['numeric_reuse_delta'] == facts
            and projection['fix']['owner_source_report'] == admission['owner_review']
            and projection['fix']['independent_source_review'] == admission['peer_review']
            and (range_fix['python_projection'] if range_fix is not None else projection['python_projection'])['path'] == fixture['runtimeManifest']
            and (range_fix['python_projection'] if range_fix is not None else projection['python_projection'])['sha256'] == fixture['runtimeManifestSha256'], 'The actual matched Python/full source projection closure differs.')
    old_full, new_full = load(old_entry['full_projection']), load(range_fix['complete_candidate'] if range_fix is not None else projection['complete_candidate'])
    require(set(old_full['files']) == set(new_full['files']) and len(old_full['files']) == 173
            and [name for name in old_full['files'] if old_full['files'][name] != new_full['files'][name]] == ['animation_composition_export.py'],
            'The complete candidate changed Unity or another product source.')
    old_config = next(load(row) for path, row in files.items() if path.name == 'first_stage_config.json')
    old_native = old_config['tools']['verify_character_composition_native']
    require(old_native['sha256'] == OLD_NATIVE_SHA, 'The original native tool is not the pinned default driver.')
    new_native = Path(config['tools']['verify_character_composition_native']['path'])
    inverse = new_native.read_text(encoding='utf-8').replace('after_numeric=None, cases_runner=None', 'after_numeric=None')
    inverse = inverse.replace("    case_runner = _run_fresh_cases if cases_runner is None else cases_runner\n    require(callable(case_runner), 'cases_runner is an explicit test-only continuation callable.')\n", '')
    inverse = inverse.replace('        case_runner(config, report, workspace_path', '        _run_fresh_cases(config, report, workspace_path')
    require(inverse == pin(old_native).read_text(encoding='utf-8'), 'Native default bodies changed beyond the explicit cases_runner seam.')
    for key in ('physicalFixture', 'loopAuthorFixture'):
        current, prior = load(config[key]), load(old_config[key])
        require({k: v for k, v in current.items() if k != 'evidenceDirectory'} ==
                {k: v for k, v in prior.items() if k != 'evidenceDirectory'}, 'Publication/Loop source authorization changed.')
    return old, fixture, facts, tail


def run(config, admission, report):
    import bpy
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    driver = importlib.import_module('run_character_composition_first_stage')
    require(sha(driver.__file__) == DRIVER_SHA, 'The original first-stage context changed.')
    driver.validate_config(config)
    native, physical, timing = [importlib.import_module(name) for name in
        ('verify_character_composition_native', 'composition_sync_acceptance', 'composition_timing_acceptance')]
    old, fixture, facts, tail = admit(config, admission, native)
    range_fix = range_projection(admission, fixture)
    if range_fix is not None:
        report['range_fix_projection_receipt'] = admission['range_fix_projection_receipt']
    sync, author = physical.validate_config(load(config['physicalFixture'])), timing.validate_author_config(load(config['loopAuthorFixture']))
    require(len(sync['items']) == 2 and all(row['kind'] == 'SOURCE' for row in sync['items']), 'Use the same two actual SOURCE publications.')
    evidence = Path(fixture['evidenceDirectory']).resolve()
    outputs = [evidence / 'composition_native_report.json', evidence / 'initial_workspace.json',
        Path(sync['evidenceDirectory']), Path(author['evidenceDirectory']), Path(config['loopSyncDirectory']), Path(config['qaSeedFile'])]
    require(len(set(outputs)) == len(outputs) and all(path.is_relative_to(evidence) and path != evidence and not path.exists() for path in outputs), 'Every continuation output must be fresh and owned.')
    if tail:
        for row in tail['current_source_pins']:
            pin(row)
    Path(author['evidenceDirectory']).mkdir(parents=True)
    with outputs[1].open('xb') as stream:
        stream.write(pin({'path': fixture['compositionWorkspace'], 'sha256': fixture['compositionWorkspaceSha256']}).read_bytes())
    driver.check_memory(report, 'before_fresh_ownership_and_import')
    def before_worker(_context, item):
        driver.check_memory(report, 'before_worker_' + item.work_item_id)
    def after_numeric(context, numeric):
        if tail:
            report['two_a_physical'] = tail['physical']
            report.update(physical_tail=tail['admission'], physical_source_publications_inherited=2,
                          physical_source_publications_executed=0, first_two_source_reexport_performed=False)
        else:
            report['two_a_physical'] = physical.run(context, sync, numeric, before_worker=before_worker)
        driver.check_memory(report, 'before_native_loop_authoring')
        report['loop_authoring'] = native.phase(numeric, 'actual_loop_authoring', lambda: timing.author_hold_loop(context, author))
        proof = report['loop_authoring']['numeric_proof']
        report['loop_physical'] = native.phase(numeric, 'actual_loop_physical', lambda:
            physical.run_one(context, proof['authorization'], config['loopSyncDirectory'], proof, before_worker=before_worker))
        if tail:
            require(report['loop_physical']['completed'] is True and report['loop_physical']['kind'] == 'SOURCE'
                    and report['loop_physical']['newActionId'] == ''
                    and uuid.UUID(report['loop_physical']['actualWorkItemId']) == uuid.UUID(author['holdLoopWorkItemId'])
                    and uuid.UUID(report['loop_physical']['actualWorkItemId']) not in
                        {uuid.UUID(row['actualWorkItemId']) for row in tail['physical']['items']},
                    'The executed tail must be the distinct third SOURCE Loop.')
            report['physical_source_publications_executed'] = 1
            for row in tail['inherited_files']:
                pin(row)
            report['first_two_link_revisions_unchanged'] = True
        if tail and tail['short']:
            # These are new UI rows, not another execution of the inherited numerics.
            modules = {name: importlib.import_module('character_designer.' + name) for name in
                ('animation_worklist', 'animation_link', 'animation_link_source', 'animation_worklist_fingerprint',
                 'animation_composition', 'animation_composition_export')}
            worklist, links, source, fp, backend, export = modules.values()
            saved, rig = worklist.state(context), worklist.state(context).rig
            loop_id = numeric['actual_loop_item_id']
            loop_anchor = native.capture_import_anchor(context, rig, loop_id, worklist, fp)
            imported_ids, anchors = [], []
            specs = [load(row) for row in fixture['specFiles']]
            for index, spec in enumerate(specs):
                rows = [row for row in saved.composition_catalog if row.ready
                        and uuid.UUID(row.work_item_id) == uuid.UUID(spec['editWorkItemId'])]
                require(len(rows) == 1, 'The exact UI Pose route is missing or ambiguous after Loop publication.')
                clip_key = str(rows[0].clip_key)
                del rows
                item = native.phase(numeric, 'current_ui_pose_import_a' + str(index + 1), lambda:
                    backend.add_work_item(context, clip_key, activate_new=False))
                item_id = str(item.item_id)
                del item
                imported_ids.append(item_id)
                anchors.append(native.capture_import_anchor(context, rig, item_id, worklist, fp))
                native.reacquire_work_item(context, rig, loop_id, worklist, anchor=loop_anchor, fingerprint=fp)
                for previous_id, previous_anchor in zip(imported_ids, anchors):
                    native.reacquire_work_item(context, rig, previous_id, worklist, anchor=previous_anchor, fingerprint=fp)
            for index, (item_id, anchor, spec) in enumerate(zip(imported_ids, anchors, specs)):
                item = native.reacquire_work_item(context, rig, item_id, worklist, anchor=anchor, fingerprint=fp)
                packet = backend._packet_for_item(item)
                scope = export.resolve_scope(context, rig, spec, packet)
                current = {'work_item_id': spec['editWorkItemId'], 'model_sha256': item.model_sha256,
                    'packet_sha256': packet['_sha256'], 'source_rest_sha256': spec['sourceRestSha256'],
                    'original_rest_sha256': scope.proof['sourceRest'], 'motion_rest_sha256': scope.proof['motionRest'],
                    'native_count': len(scope.mapping.indices), 'mapping_error_metres': scope.mapping.error_metres}
                inherited = tail['persisted_fresh_numeric']['fresh_packet_proofs'][index]
                current_fingerprint = backend.item_fingerprint(context, item)
                inherited_case = next(row for row in old['cases'] if row['reference'] == 'a' + str(index + 1))
                require(backend.item_spec(item) == spec and current['native_count'] == 217
                        and {key: value for key, value in current.items() if key != 'mapping_error_metres'} ==
                            {key: value for key, value in inherited.items() if key != 'mapping_error_metres'}
                        and math.isfinite(current['mapping_error_metres']) and 0 <= current['mapping_error_metres'] <= .0002
                        and current_fingerprint == inherited_case['b_fingerprint'],
                        'The actual current UI row packet/native217/Rest/model differs from its inherited numeric domain.')
                del item, scope
                captured, previous = worklist._capture_rig(context, rig), source._context_state(context)
                previous_index = saved.active_index
                try:
                    worklist.activate(context, item_id, 'CUSTOM')
                    association = native.fresh_association_proof(context, rig, item_id, links, worklist,
                        fixture['compositionModelSha256'], anchor=anchor, fingerprint=fp)
                    require(association == tail['persisted_fresh_numeric']['fresh_associations'][index],
                            'The current UI row must retain the exact authenticated source/model/Link/Action slot route.')
                finally:
                    worklist._restore_rig(context, rig, captured)
                    source._restore_context(context, previous, playing=True)
                    saved.active_index = previous_index
                    context.view_layer.update()
                numeric.setdefault('currentImportedForUI', []).append({'item_id': item_id, 'packet_rest_proof': current,
                    'association': association, 'b_fingerprint': current_fingerprint,
                    'numeric_resampled': False, 'isolated_sync_executed': False,
                    'source_export_executed': False})
            native.reacquire_work_item(context, rig, loop_id, worklist, anchor=loop_anchor, fingerprint=fp)
            for item_id, anchor in zip(imported_ids, anchors):
                native.reacquire_work_item(context, rig, item_id, worklist, anchor=anchor, fingerprint=fp)
            numeric['current_ui_native_associations_verified'] = True
            # The legacy seed helper checks real current row IDs; their numeric source is explicit below.
            numeric['fresh_native_associations_verified'] = True
            for row in tail['inherited_files']:
                pin(row)
        seed_physical = str(pin(tail['physical_report'])) if tail else str(Path(sync['evidenceDirectory']) / 'composition_sync_report.json')
        report['qa_seed'] = native.phase(numeric, 'save_current_ui_seed', lambda:
            native.save_qa_seed(context, numeric, config['qaSeedFile'], physical_report_path=seed_physical))
    def cases_runner(cfg, numeric, workspace_path, rig, scene, preservation, prior_actions, prior_assets, specs, callback):
        modules = {name: importlib.import_module('character_designer.' + name) for name in
            ('animation_worklist', 'animation_link', 'animation_link_source', 'animation_worklist_fingerprint', 'animation_composition', 'animation_composition_export')}
        worklist, links, source, fp, backend, export = modules.values()
        saved = worklist.state(bpy.context)
        target = importlib.import_module('character_designer.animation_worklist_target')
        recovery_workspace = (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256,
                              rig.get('character_designer_animation_source_model_sha256'))
        recovery_association = {key: rig[key] for key in (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
        recovery_session = target.capture_session_metadata(rig)
        backend.connect_library(bpy.context, str(workspace_path))
        if tail and tail['short']:
            library_record, library = backend._library_state(bpy.context)
            loop_routes = [row for row in saved.composition_catalog if row.ready
                           and uuid.UUID(row.work_item_id) == uuid.UUID(author['holdLoopWorkItemId'])]
            pose_routes = [row for row in saved.composition_catalog if row.ready
                           and uuid.UUID(row.work_item_id) == uuid.UUID(specs[0]['editWorkItemId'])]
            require(len(loop_routes) == len(pose_routes) == 1, 'The prepared Loop and common FK bind routes must be unique.')
            loop_key, pose_key = str(loop_routes[0].clip_key), str(pose_routes[0].clip_key)
            _record, loop_clip, loop_link, loop_packet, _name, _model = worklist._prepared(library, loop_key, existing_target=True)
            _record, pose_clip, pose_link, pose_packet, _name, _model = worklist._prepared(library, pose_key, existing_target=True)
            loop_spec = loop_link['composition']
            matcher_keys = ('mask', 'allowedBonePaths', 'humanRoles', 'editableControlIds', 'sourceRestSha256',
                            'weaponRigBonePath', 'gripTargets')
            require(uuid.UUID(loop_spec['editWorkItemId']) == uuid.UUID(author['holdLoopWorkItemId'])
                    and loop_spec['timeMode'] == 'HoldLoop' and loop_spec['editNewActionId'] == ''
                    and loop_clip['composition'] == loop_packet['composition'] == loop_spec
                    and pose_clip['composition'] == pose_link['composition'] == pose_packet['composition'] == specs[0]
                    and pose_packet['_sha256'] == tail['persisted_fresh_numeric']['fresh_packet_proofs'][0]['packet_sha256']
                    and loop_packet['bones'] == pose_packet['bones']
                    and library.model_sha256 == cfg['compositionModelSha256']
                    and len(loop_spec['allowedBonePaths']) == 41
                    and all(loop_spec[key] == spec[key] for spec in specs for key in matcher_keys),
                    'The actual Loop bones/native Rest and FK matcher domain must equal the inherited Pose domain.')
            loop_scope = export.resolve_scope(bpy.context, rig, loop_spec, loop_packet)
            inherited = tail['persisted_fresh_numeric']['fresh_packet_proofs'][0]
            require(len(loop_scope.mapping.indices) == cfg['expectedNativeCount'] == 217
                    and loop_scope.proof['sourceRest'] == inherited['original_rest_sha256']
                    and loop_scope.proof['motionRest'] == inherited['motion_rest_sha256']
                    and math.isfinite(loop_scope.mapping.error_metres) and 0 <= loop_scope.mapping.error_metres <= .0002,
                    'The actual Loop must retain the current strict native217 bind/Rest mapping before Add.')
            numeric['actual_loop_bind_preconditions'] = {'work_item_id': loop_spec['editWorkItemId'],
                'packet_sha256': loop_packet['_sha256'], 'native_count': len(loop_scope.mapping.indices),
                'mapping_error_metres': loop_scope.mapping.error_metres, 'full_bones_metadata_equal': True,
                'fk_matcher_fields_equal': list(matcher_keys), 'source_rest_sha256': loop_scope.proof['sourceRest'],
                'motion_rest_sha256': loop_scope.proof['motionRest']}
            del loop_routes, pose_routes, loop_scope
            loop_item = native.phase(numeric, 'actual_loop_import', lambda:
                backend.add_work_item(bpy.context, loop_key, activate_new=False))
            loop_id = str(loop_item.item_id)
            del loop_item
            numeric['actual_loop_item_id'] = loop_id
            loop_anchor = native.capture_import_anchor(bpy.context, rig, loop_id, worklist, fp)
            native.reacquire_work_item(bpy.context, rig, loop_id, worklist, anchor=loop_anchor, fingerprint=fp)
            native.assert_actions(prior_actions, fp)
            require(not preservation.protected_preview_changes(prior_assets,
                    preservation.asset_snapshot(scene, rig, importlib.import_module('character_designer.unity_animation'))),
                    'The actual Loop import changed protected artist assets.')
            numeric.update(cases=old['cases'], effective_values=old['effective_values'], numeric_completed=True,
                same_b_across_two_a_seek_and_object_motion=True,
                fresh_packet_proofs=tail['persisted_fresh_numeric']['fresh_packet_proofs'],
                fresh_associations=tail['persisted_fresh_numeric']['fresh_associations'],
                fresh_packet_proofs_source=tail['admission']['native_report'],
                fresh_isolated_variants_inherited=2, fresh_isolated_variants_executed=0,
                protected_prior_actions_unchanged=True, protected_assets_unchanged=True,
                historical_recovery_association_unchanged=True, worker_launch_performed=False,
                numeric_reuse={'source': admission['numeric_report'], 'old_report_completed': False,
                    'cases_reexecuted': False, 'fresh_isolated_variants_inherited': 2, 'fresh_isolated_variants_executed': 0,
                    'persisted_fresh_native_source': tail['admission']['native_report'], 'serializer_delta': facts})
            if range_fix is not None:
                numeric['numeric_reuse'].update(range_fix_projection_receipt=admission['range_fix_projection_receipt'],
                    range_fix_delta={'changed_functions': range_fix['fix']['changed_functions'],
                        'previous_projection': range_fix['previous_projections'][0],
                        'projection': range_fix['python_projection'], 'timing_admission_only': True})
            report.update(fresh_isolated_variants_inherited=2, fresh_isolated_variants_executed=0)
            callback(bpy.context, numeric)
            native.assert_actions(prior_actions, fp)
            require(len(numeric['currentImportedForUI']) == 2 and numeric['current_ui_native_associations_verified'] is True
                    and recovery_workspace == (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256,
                        rig.get('character_designer_animation_source_model_sha256'))
                    and recovery_association == {key: rig[key] for key in (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
                    and recovery_session == target.capture_session_metadata(rig)
                    and not preservation.protected_preview_changes(prior_assets,
                        preservation.asset_snapshot(scene, rig, importlib.import_module('character_designer.unity_animation'))),
                    'The actual Loop/UI seed route changed its protected assets or captured recovery association/session.')
            return
        ids, anchors = [], []
        for index, spec in enumerate(specs):
            rows = [row for row in saved.composition_catalog if row.ready and uuid.UUID(row.work_item_id) == uuid.UUID(cfg['bWorkItemIds'][index])]
            require(len(rows) == 1, 'Fresh B route is missing or ambiguous.')
            item = backend.add_work_item(bpy.context, rows[0].clip_key, activate_new=False)
            item_id = str(item.item_id)
            ids.append(item_id)
            anchors.append(native.capture_import_anchor(bpy.context, rig, item_id, worklist, fp))
        native.assert_actions(prior_actions, fp)
        require(not preservation.protected_preview_changes(prior_assets, preservation.asset_snapshot(scene, rig, importlib.import_module('character_designer.unity_animation'))), 'Imports changed protected assets.')
        for index, (item_id, anchor, spec) in enumerate(zip(ids, anchors, specs)):
            item = native.reacquire_work_item(bpy.context, rig, item_id, worklist, anchor=anchor, fingerprint=fp)
            packet, scope = backend._packet_for_item(item), None
            scope = export.resolve_scope(bpy.context, rig, spec, packet)
            fresh = {'work_item_id': spec['editWorkItemId'], 'model_sha256': item.model_sha256,
                'packet_sha256': packet['_sha256'], 'source_rest_sha256': spec['sourceRestSha256'],
                'original_rest_sha256': scope.proof['sourceRest'], 'motion_rest_sha256': scope.proof['motionRest'],
                'native_count': len(scope.mapping.indices), 'mapping_error_metres': scope.mapping.error_metres}
            require(fresh == old['fresh_packet_proofs'][index] and backend.item_spec(item) == spec, 'Fresh native217/Rest/model/B packet/spec differs.')
            numeric.setdefault('fresh_packet_proofs', []).append(fresh)
            captured, previous = worklist._capture_rig(bpy.context, rig), source._context_state(bpy.context)
            previous_index = saved.active_index
            try:
                worklist.activate(bpy.context, item_id, 'CUSTOM')
                association = native.fresh_association_proof(bpy.context, rig, item_id, links, worklist, cfg['compositionModelSha256'], anchor=anchor, fingerprint=fp)
                require(association == old['fresh_associations'][index], 'Fresh association differs from the original route.')
                numeric.setdefault('fresh_associations', []).append(association)
                backend.enter(bpy.context, spec, item_id)
                with backend.isolated_sync(bpy.context, rig) as inputs:
                    model_helpers = export.baseline_worker()._model_helpers()
                    native_names = {bone.name for bone in inputs['rig'].data.bones if not model_helpers._is_control(bone)}
                    require(len(native_names) == cfg['expectedNativeCount'] == 217
                            and native_names == set(inputs['scope'].mapping.indices), 'The fresh isolated output must retain all 217 proved native bones.')
                    original = next(row for row in old['cases'] if row['reference'] == 'a' + str(index + 1))
                    require(inputs['effective_values'] == old['effective_values'] and inputs['b_fingerprint'] == original['b_fingerprint']
                            and inputs['composition_export'] == original['composition_export'], 'Fresh isolated B/input/A proof differs from immutable numerics.')
            finally:
                if backend.active(rig):
                    backend.exit(bpy.context, rig, writeback=False)
                worklist._restore_rig(bpy.context, rig, captured)
                source._restore_context(bpy.context, previous, playing=True)
                saved.active_index = previous_index
                bpy.context.view_layer.update()
            native.reacquire_work_item(bpy.context, rig, item_id, worklist, anchor=anchor, fingerprint=fp)
        require(recovery_workspace == (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256,
                    rig.get('character_designer_animation_source_model_sha256'))
                and recovery_association == {key: rig[key] for key in (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
                and recovery_session == target.capture_session_metadata(rig), 'Fresh verification changed its captured recovery workspace/association/session.')
        numeric.update(cases=old['cases'], effective_values=old['effective_values'], numeric_completed=True,
            same_b_across_two_a_seek_and_object_motion=True, protected_prior_actions_unchanged=True,
            protected_assets_unchanged=True, fresh_native_associations_verified=True, historical_recovery_association_unchanged=True,
            worker_launch_performed=False, numeric_reuse={'source': admission['numeric_report'], 'old_report_completed': False,
                'cases_reexecuted': False, 'fresh_isolated_variants_verified': 2, 'serializer_delta': facts})
        native.assert_actions(prior_actions, fp)
        require(not preservation.protected_preview_changes(prior_assets, preservation.asset_snapshot(scene, rig, importlib.import_module('character_designer.unity_animation'))), 'Fresh isolation changed protected assets.')
        callback(bpy.context, numeric)
    report['numeric'] = {'schema': native.REPORT_SCHEMA, 'completed': False, 'fixture': str(pin(config['nativeFixture'])),
        'fixture_sha256': config['nativeFixture']['sha256'], 'initial_workspace_snapshot': {'path': str(outputs[1]), 'sha256': fixture['compositionWorkspaceSha256']}}
    try:
        native.run(fixture, report['numeric'], after_numeric=after_numeric, cases_runner=cases_runner)
    finally:
        with outputs[0].open('x', encoding='utf-8') as stream:
            json.dump(report['numeric'], stream, ensure_ascii=False, allow_nan=False, indent=2)
    require(report['numeric']['completed'] is True, 'Fresh historical outer recovery did not complete.')
    report.update(completed=True, artist_save_performed=False, original_scene_recovery_verified=True, source_packets_rewritten=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--reuse-admission', required=True)
    parser.add_argument('--reuse-admission-sha256', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:])
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    admission = load({'path': args.reuse_admission, 'sha256': args.reuse_admission_sha256})
    destination = Path(load(config['nativeFixture'])['evidenceDirectory']) / 'composition_first_stage_report.json'
    require(not destination.exists(), 'Never overwrite a prior continuation report.')
    report = {'schema': 'x.validation.character-composition-first-stage-report/1', 'completed': False,
        'config': str(config_path), 'config_sha256': sha(config_path), 'continuation': True,
        'reuse_admission': {'path': str(Path(args.reuse_admission).resolve()), 'sha256': args.reuse_admission_sha256}}
    try:
        run(config, admission, report)
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)


if __name__ == '__main__':
    main()
