"""SOURCE-only n12 fixture construction; run only after independent source review.

This copies pinned n11 inputs and freezes a reviewed continuation entry. It
grants no execution permission and never imports Blender or acceptance tools.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(r'D:/Blender/Projects/Character/X/validation/composition_20261011')
TOOLS = Path(__file__).resolve().parent
OLD_CONFIG = '68830ac302d6951cd65844a0941b9792f7a4fe55214558caf1c82f1bf1dc16f7'
OLD_ENTRY = '4a916f5d407b6ccfd2442f2408d6248bd0392995958c8c63610822f54dcb687e'
FAILURE = '2291e1e7360b8431eafb0d2fd86b12eafe486d96a2760b58051867468a0a6189'
PROJECTION = 'cbf7528f2c014389ba3b4d29adc359e7dd89ec25e460b5cf51674f34ef8145cb'
CONTINUATION = '0da0072321607a2e5e31974a8bc4889d04fcb670430b8cebf0dd7443e231f2f9'
NATIVE = '1ebf29f7ac4358884a3861164aa0e9894db37da57f74fe64a5ef3493b650ad32'
OWNER = 'd1e953f6a5f15f8d9c4a710a74077ec3839e71bd1dd66c803545dada885949b6'
PEER = 'fb442180f48c65304ffa019e0ef7502d9dbcbcbfd0784ad8e3b464d3a9bd66a1'
CACHE = '0a09c09f9e965b530fa34270548989301e29f3b20792ea6307f8ddc71eda1234'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build(args):
    admitted, planned = {}, {}

    def read(row):
        path = Path(row['path']).resolve()
        require(Path(row['path']).is_absolute() and re.fullmatch('[0-9a-f]{64}', row['sha256'])
                and path.is_file() and digest(path) == row['sha256'], 'Changed pinned input: ' + str(path))
        size = path.stat().st_size
        require('bytes' not in row or row['bytes'] == size, 'Changed input byte count.')
        admitted[str(path)] = row['sha256']
        return {'path': str(path), 'sha256': row['sha256'], 'bytes': size}

    def load(row):
        pin = read(row)
        value = json.loads(Path(pin['path']).read_bytes().decode('utf-8'))
        json.dumps(value, allow_nan=False)
        return value

    def fixed(relative, sha256):
        return {'path': str(ROOT / relative), 'sha256': sha256}

    def config_pin(row):
        return {key: row[key] for key in ('path', 'sha256')}

    def plan(path, value=None, raw=None):
        path = Path(path).resolve()
        require(path.is_relative_to(destination) and not path.exists(), 'Fresh owned output required.')
        raw = raw if raw is not None else (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
        require(path not in planned, 'Duplicate output path.')
        planned[path] = raw
        return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}

    producer = read({'path': str(Path(__file__).resolve()), 'sha256': args.producer_sha256})
    destination = ROOT / 'n12'
    require(not destination.exists(), 'Never overwrite an earlier n12 attempt.')
    require(len(str(destination / 'DATAFILES').encode('utf-16-le')) // 2 + 174 < 260, 'Owned source-cache path exceeds its budget.')
    old_config_pin, old_entry_pin = fixed('n11/first_stage_config.json', OLD_CONFIG), fixed('n11/source_entry_receipt.json', OLD_ENTRY)
    config, entry = load(old_config_pin), load(old_entry_pin)
    failure_pin = read(fixed('n11_natural_failure_readback_c5d1170c.json', FAILURE))
    failure = load(failure_pin)
    require(failure['evidence_readback_passed'] is True and failure['native_run_completed'] is False
            and failure['full_acceptance_passed'] is False and all(failure['checks'].values())
            and len(failure['source_rehash']) == 28, 'Require the actual partial-numeric/failed-worker readback.')
    for row in failure['files']:
        read(row)
    for row in failure['source_rehash']:
        require(row['matches_expected'] is True and entry['source_inputs'][row['path']] == row['sha256'], 'Unadmitted original source.')
        read(row)
    numeric_pin = next(read(row) for row in failure['files'] if Path(row['path']).name == 'composition_native_report.json')
    numeric = load(numeric_pin)
    require(numeric['numeric_completed'] is True and numeric['completed'] is False, 'Do not relabel n11 as completed.')
    projection_pin = read(fixed('composition_snapshot_serializer_projection_receipt_11_12.json', PROJECTION))
    projection = load(projection_pin)
    require(projection['source_only'] is True and projection['native_execution_performed'] is False
            and projection['release_or_deployment_performed'] is False and projection['embedded_overlays_unchanged'] is True
            and projection['embedded_overlay_count'] == 8, 'Require actual SOURCE-only serializer projection.')
    cache_pin = read(fixed('n11_runtime10_observed_bytecode_provenance_116.json', CACHE))
    cache = load(cache_pin)
    require(cache['schema'] == 'x.validation.character-composition-old-runtime-bytecode-provenance/1'
            and cache['source_only'] is True and cache['native_execution_performed'] is False
            and cache['bytecode_imported_unmarshalled_or_executed'] is False and cache['source_file_count'] == 167
            and cache['runtime_projection'] == projection['previous_projections'][0]
            and cache['cache_file_count'] == len(cache['cache_files']) == 116, 'Use the exact observed old bytecode provenance.')
    for old_pin, new_pin, count in zip(projection['previous_projections'],
            [projection['python_projection'], projection['complete_candidate']], [167, 173]):
        prior, current = load(old_pin), load(new_pin)
        require(old_pin['sha256'] == entry['runtime_projection' if count == 167 else 'full_projection']['sha256']
                and set(prior['files']) == set(current['files']) and len(current['files']) == count
                and [name for name in prior['files'] if prior['files'][name] != current['files'][name]] == ['animation_composition_export.py'], 'Product scope changed.')
        for manifest_pin, manifest in [(old_pin, prior), (new_pin, current)]:
            package = Path(manifest_pin['path']).parent / 'character_designer'
            allowed = set()
            if count == 167 and manifest_pin['sha256'] == cache['runtime_projection']['sha256']:
                for row in cache['cache_files']:
                    relative = Path(row['relative_path'])
                    source = (relative.parent.parent / (relative.name[:-len('.cpython-313.pyc')] + '.py')).as_posix()
                    require(relative.parent.name == '__pycache__' and relative.name.endswith('.cpython-313.pyc')
                            and source == row['source_relative_path'] and source in manifest['files']
                            and row['source_sha256'] == manifest['files'][source]
                            and Path(row['path']).resolve() == (package / relative).resolve(), 'Unproved old bytecode/source association.')
                    read(row); allowed.add(row['relative_path'])
                require(len(allowed) == 116, 'Duplicate old bytecode path.')
            require({str(p.relative_to(package)).replace('\\', '/') for p in package.rglob('*') if p.is_file()} == set(manifest['files']) | allowed, 'Projection inventory differs.')
            for name, sha256 in manifest['files'].items():
                read({'path': str(package / name), 'sha256': sha256})
    require(projection['embedded_overlay_manifest']['sha256'] == entry['embedded_overlay_manifest']['sha256'], 'Embedded Unity overlay changed.')
    read(projection['embedded_overlay_manifest'])
    for field in ('owner_source_report', 'independent_source_review', 'focused_pure_test_report'):
        review = load(projection['fix'][field])
        require(review['source_only'] is True and review['native_execution_performed'] is False and review['passed'] is True, 'Serializer review is not complete.')
    owner_pin = read(fixed('native_continuation_source_report_20261011_0da00723.json', OWNER))
    require(args.continuation_peer_sha256 == PEER and Path(args.continuation_peer).resolve() ==
            ROOT / 'native_continuation_peer_source_review_20261011_0da00723.json', 'Require the actual current independent continuation peer.')
    peer_pin = read({'path': args.continuation_peer, 'sha256': args.continuation_peer_sha256})
    owner, peer = load(owner_pin), load(peer_pin)
    for review in (owner, peer):
        require(review['source_only'] is True and review['native_execution_performed'] is False and review['passed'] is True
                and review['native_seam']['inverse_normalized_source_exact'] is True
                and review['native_seam']['default_numeric_function_bodies_unchanged'] is True, 'Require strict continuation owner and peer SOURCE proof.')
        pins = {Path(row['path']).name: read(row) for row in review['source_files']}
        require(pins['run_character_composition_first_stage_continuation.py']['sha256'] == CONTINUATION
                and pins['verify_character_composition_native.py']['sha256'] == NATIVE, 'Continuation review pins differ.')
    require(owner['native_seam'] == peer['native_seam'], 'Default-body proof differs between reviewers.')
    old_native = read(config['tools']['verify_character_composition_native'])
    new_native = read({'path': str(TOOLS / 'verify_character_composition_native.py'), 'sha256': NATIVE})
    inverse = Path(new_native['path']).read_text(encoding='utf-8').replace('after_numeric=None, cases_runner=None', 'after_numeric=None')
    inverse = inverse.replace("    case_runner = _run_fresh_cases if cases_runner is None else cases_runner\n    require(callable(case_runner), 'cases_runner is an explicit test-only continuation callable.')\n", '')
    inverse = inverse.replace('        case_runner(config, report, workspace_path', '        _run_fresh_cases(config, report, workspace_path')
    require(inverse == Path(old_native['path']).read_text(encoding='utf-8'), 'Native default source was modified.')
    manifest = load(entry['tools_source_manifest'])
    require(len(manifest['tools']) == 10, 'Use the exact previous ten-tool manifest.')
    sources = deepcopy(manifest['tools'])
    sources['verify_character_composition_native'] = new_native
    sources['run_character_composition_first_stage_continuation'] = read({'path': str(TOOLS / 'run_character_composition_first_stage_continuation.py'), 'sha256': CONTINUATION})
    for row in sources.values():
        read(row)
    frozen = {}
    for name, row in entry['tools'].items():
        row = new_native if name == 'verify_character_composition_native' else read(row)
        frozen[name] = plan(destination / 'tools' / (name + '.py'), raw=Path(row['path']).read_bytes())
    name = 'run_character_composition_first_stage_continuation'
    frozen[name] = plan(destination / 'tools' / (name + '.py'), raw=Path(sources[name]['path']).read_bytes())
    native = load(config['nativeFixture'])
    native.update(addonPackage=str(Path(projection['python_projection']['path']).parent / 'character_designer'),
                  runtimeManifest=projection['python_projection']['path'], runtimeManifestSha256=projection['python_projection']['sha256'], evidenceDirectory=str(destination))
    for row in native['specFiles']:
        read(row)
    config['nativeFixture'] = config_pin(plan(destination / 'native_fixture.json', native))
    for key, directory, filename in [('physicalFixture', 'physical', 'physical_fixture.json'), ('loopAuthorFixture', 'loop_author', 'loop_author_fixture.json')]:
        fixture = load(config[key]); fixture['evidenceDirectory'] = str(destination / directory)
        config[key] = config_pin(plan(destination / filename, fixture))
    config['tools'] = {name: config_pin(frozen[name]) for name in config['tools']}
    config.update(loopSyncDirectory=str(destination / 'loop_sync'), qaSeedFile=str(destination / 'qa_seed.blend'))
    stage_pin = plan(destination / 'first_stage_config.json', config)
    admission = {'schema': 'x.validation.character-composition-numeric-reuse/1', 'numeric_report': numeric_pin,
                 'failure_readback': failure_pin, 'owner_review': deepcopy(projection['fix']['owner_source_report']),
                 'peer_review': deepcopy(projection['fix']['independent_source_review']), 'projection_receipt': projection_pin}
    admission_pin = plan(destination / 'reuse_admission.json', admission)
    bridge = {'schema': 'x.validation.character-composition-native-continuation-bridge/1', 'source_only': True,
              'native_execution_performed': False, 'passed': True, 'native_pending': True, 'source_files': list(sources.values()),
              'source_file': new_native, 'owner_source_report': owner_pin, 'independent_source_review': peer_pin,
              'native_seam': owner['native_seam'], 'preserved_default_native_source': old_native,
              'source_generation': 'Owner/peer bindings and independent exact inverse-source comparison; no acceptance tool imported or run.'}
    bridge_pin = plan(destination / 'native_source_report.json', bridge)
    manifest_pin = plan(destination / 'tools_source_manifest.json', {'schema': 'x.validation.character-composition-tools-source/1',
        'source_only': True, 'native_execution_performed': False, 'native_pending': True, 'tools': sources,
        'origin_manifest': entry['tools_source_manifest'], 'delta': {'updated': ['verify_character_composition_native'], 'added': [name]},
        'owner_source_report': owner_pin, 'independent_source_review': peer_pin, 'projection_receipt': projection_pin,
        'historical_test_policy': 'Owner authored eleven tests and compiled sources; tests and native are not executed by this producer.'})
    for path, sha256 in entry['source_inputs'].items():
        if Path(path).resolve() == Path(new_native['path']):
            continue
        read({'path': path, 'sha256': sha256})
    for path, sha256 in list(admitted.items()):
        require(digest(path) == sha256, 'Input changed during preparation: ' + path)
    entry.update(source_only=True, native_execution_performed=False, launch_permission_currently_pending=True,
                 config=config_pin(stage_pin), runtime_projection=config_pin(projection['python_projection']), full_projection=config_pin(projection['complete_candidate']),
                 source_builder=producer, native_source_report=bridge_pin, tools_source_manifest=manifest_pin, tools=frozen,
                 old_runtime_bytecode_provenance=cache_pin,
                 snapshot_serializer_projection_receipt=projection_pin, continuation={'entry': frozen[name], 'admission': admission_pin},
                 origin_source_entry=read(old_entry_pin), origin_first_stage_config=read(old_config_pin),
                 source_inputs={**admitted, **{str(p): hashlib.sha256(b).hexdigest() for p, b in planned.items()}},
                 qaToolChange='Seven frozen files; unchanged driver and five config helper keys. Explicit numeric-reuse admission and fresh SOURCE/physical recovery remain pending native authorization.')
    plan(destination / 'source_entry_receipt.json', entry)
    require(len(frozen) == 7 and len(sources) == 11 and len(config['tools']) == 5 and native['sessionChecks'] is None, 'Unexpected helper or fixture scope.')
    if not args.write:
        return {'source_only': True, 'native_execution_performed': False, 'files_planned': len(planned), 'write_performed': False}
    destination.mkdir()
    for name in ('CONFIG', 'DATAFILES'):
        (destination / name).mkdir()
    for path, raw in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
    return {'source_only': True, 'native_execution_performed': False, 'config': stage_pin,
            'source_entry': {'path': str(destination / 'source_entry_receipt.json'), 'sha256': digest(destination / 'source_entry_receipt.json')}, 'continuation': entry['continuation']}


def build_tail(args):
    """Freeze only the remaining Loop/seed path, retaining both real publications."""
    destination = ROOT / 'n15'
    require(not destination.exists(), 'Never overwrite a prior Loop-tail attempt.')
    admitted, planned = {}, {}
    range_fix = None

    def read(row):
        # A historical canonical exporter pin is verified only through its exact
        # immutable runtime11 leaf. Current canonical bytes have a separate pin.
        if range_fix is not None and (Path(row['path']).resolve() ==
                Path(range_fix['fix']['superseded_canonical_source_path']).resolve()
                and row['sha256'].lower() == range_fix['fix']['superseded_source']['sha256']):
            row = range_fix['fix']['superseded_source']
        path, expected = Path(row['path']).resolve(), row['sha256'].lower()
        require(Path(row['path']).is_absolute() and re.fullmatch('[0-9a-f]{64}', expected)
                and path.is_file() and digest(path) == expected, 'Changed pinned tail input: ' + str(path))
        size = path.stat().st_size
        require('bytes' not in row or row['bytes'] == size, 'Changed tail input byte count.')
        require(str(path) not in admitted or admitted[str(path)] == expected, 'Conflicting tail source pin.')
        admitted[str(path)] = expected
        return {'path': str(path), 'sha256': expected, 'bytes': size}

    def load(row):
        record = read(row)
        value = json.loads(Path(record['path']).read_text(encoding='utf-8'))
        json.dumps(value, allow_nan=False)
        return value

    def fixed(relative, sha256):
        return {'path': str(ROOT / relative), 'sha256': sha256}

    def short(row):
        return {key: row[key] for key in ('path', 'sha256')}

    def plan(path, value=None, raw=None):
        path = Path(path).resolve()
        require(path.is_relative_to(destination) and not path.exists() and path not in planned, 'Fresh owned tail output required.')
        raw = raw if raw is not None else (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
        planned[path] = raw
        return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}

    producer = read({'path': str(Path(__file__).resolve()), 'sha256': args.producer_sha256})
    previous = {
        'first_stage_report': fixed('n12/composition_first_stage_report.json', '916860cd83d4c2eafbc346017659855d96ba42f1f3bcaeea2fb182e365c4467d'),
        'native_report': fixed('n12/composition_native_report.json', '32d5178e73f2900d21add8518ec6fd45fc4df40c41586f11a4abb00257d06eb9'),
        'physical_report': fixed('n12/physical/composition_sync_report.json', 'd1624f1b737b90c162a0fb1d0436102c59b20d20acf46b675fe9c675f179d5af'),
        'loop_failure': fixed('n12/loop_author/composition_HoldLoop_authoring_report.json', '67d00b7fe4529e60eeacd7aff58f40d7a268414b92674ff7b58b80e7e9f50a43'),
        'failure_readback': fixed('n12_natural_failure_readback_916860cd.json', '041253e90a8baae9ed715c28b4fde0e002aeea3e57ff595b41911e08011f5ff7'),
        'source_entry': fixed('n12/source_entry_receipt.json', '845394a52e7ba286c20fe100f1bbd69a559ae7771678b3589b906bb3acec31ec')}
    old = {name: load(row) for name, row in previous.items()}
    config = load(fixed('n12/first_stage_config.json', '2705b17944deedfb4999d9c6449b1e8421f0d7df9b36d26f2b33f4c8c90aa41e'))
    latest = {
        'latest_failure_readback': fixed('n14_natural_failure_readback_43b9cf86.json', '829c2758f1e7b33e52ed46571891a55d2dc31313a9816fc9cb951f607e459488'),
        'latest_exit': fixed('n14/process_exit.json', '04a8fb7185d7080169bbb8708ff6f5838269d319a6f30a9492a38e29baf14bc2'),
        'latest_source_entry': fixed('n14/source_entry_receipt.json', 'f2b38a3aca7639618396c00db45c8d9a6e25020bc3d71321a55feea1a51ad1cc')}
    latest_values = {name: load(row) for name, row in latest.items()}
    crash, crash_exit, crash_entry = (latest_values[name] for name in ('latest_failure_readback', 'latest_exit', 'latest_source_entry'))
    require(crash['schema'] == 'x.validation.character-composition-native-failure-protection/1'
            and crash['attempt'] == 'n14' and crash['completed'] is False and crash['native_process_performed'] is True
            and crash['native_process_natural_exit_code'] == 1 and crash['source_input_count'] == 964
            and crash['source_inputs_all_unchanged'] is True and crash['artist_file_unchanged'] is True
            and crash['artist_mutation_performed'] is False and crash['full_outer_native_recovery_completed'] is False
            and crash['historical_native_data_rest_FK_action_slot_rows_session_association_restored'] is True
            and crash['native_rotate_finished_count'] == 1 and crash['native_keyframe_insert_count'] == 3
            and crash['loop_authoring_native_keys_and_commit_proven'] is True
            and crash['actual_failure_phase'] == 'measure_genuine_foreground_B_for_physical_Sync'
            and crash['actual_error'] == 'Moving B sample bounds exceed its prepared duration or authored selected-slot FK range.'
            and crash['loop_authoring_complete'] is False and crash['loop_export_performed'] is False
            and crash['owned_qa_seed_created'] is False and crash['current_UI_imports_executed'] == 0
            and crash['automatic_retry_or_forced_termination_performed'] is False and crash['u1_generated'] is False
            and len(crash['actual_absent_success_paths']) == 5
            and all(not Path(path).exists() for path in crash['actual_absent_success_paths'])
            and crash['actual_process_exit_values'] == crash_exit and crash_exit['pid'] == 65540
            and crash_exit['naturalExitCode'] == 1 and crash_exit['forcedTermination'] is False
            and crash_exit['allQaBlenderWorkersGoneVerified'] is True
            and crash_exit['osInventoryError'] is None and crash_exit['launchReceiptError'] is None
            and len(crash_exit['remainingBlenderProcesses']) == 1
            and crash_exit['remainingBlenderProcesses'][0]['ProcessId'] == 27676,
            'Retain actual n14 keys, failed range admission and protected original artist; never infer Loop completion.')
    require(crash['source_entry']['sha256'] == latest['latest_source_entry']['sha256']
            and crash['actual_process_exit']['sha256'] == latest['latest_exit']['sha256']
            and len(crash_entry['source_inputs']) == len(crash['source_input_checks']) == 964
            and {row['path']: row['expected_sha256'] for row in crash['source_input_checks']} == crash_entry['source_inputs']
            and all(row['actual_sha256'] == row['expected_sha256'] for row in crash['source_input_checks']),
            'The exact failed attempt source inventory must match its actual protection readback.')
    for row in crash['files']:
        read(row)
    entry = deepcopy(old['source_entry'])
    require(old['first_stage_report']['completed'] is False and old['native_report']['completed'] is False
            and old['native_report']['numeric_completed'] is True and old['loop_failure']['complete'] is False
            and old['first_stage_report']['two_a_physical'] == old['physical_report'], 'Keep n12 failure and real two-Pose evidence exact.')
    failure = old['failure_readback']
    require(failure['source_only'] is True and failure['passed'] is True and failure['source_input_count'] == 903
            and failure['source_inputs_unchanged'] == 901 and failure['actual_two_pose_exports_complete'] is True
            and failure['loop_authoring_complete'] is False and failure['qa_seed_created'] is False
            and failure['natural_exit_record']['naturalExitCode'] == 1
            and failure['natural_exit_record']['allQaBlenderWorkersGoneVerified'] is True
            and failure['natural_exit_record']['forcedTermination'] is False, 'Require actual natural n12 failure closure.')
    owner_pin = read({'path': args.tail_owner, 'sha256': args.tail_owner_sha256})
    peer_pin = read({'path': args.continuation_peer, 'sha256': args.continuation_peer_sha256})
    owner, peer = load(owner_pin), load(peer_pin)
    expected_before = {str(TOOLS / name): value for name, value in {
        'build_character_composition_continuation_fixture.py': '239a2b187106dc5592da74fc2bae12c5e37df87356ffc233cd50f4646ecef339',
        'run_character_composition_first_stage_continuation.py': CONTINUATION,
        'composition_timing_acceptance.py': '58325c8b4256585a12370aa10a059eabb1d80c74fe3d2eb4b2fbb97fecf0cb06'}.items()}
    changes = owner['reviewed_source_changes']
    require(changes == peer['reviewed_source_changes'] and set(changes) == set(expected_before), 'Both reviewers must bind exactly three tail tools.')
    for review in (owner, peer):
        require(review['source_only'] is True and review['native_execution_performed'] is False and review['passed'] is True
                and review['native_seam']['inverse_normalized_source_exact'] is True
                and review['native_seam']['default_numeric_function_bodies_unchanged'] is True, 'Require actual independent tail SOURCE review.')
    for path, delta in changes.items():
        require(set(delta) == {'before_sha256', 'after_sha256'} and delta['before_sha256'] == expected_before[path]
                and entry['source_inputs'][path] == delta['before_sha256'], 'Unreviewed tail source replacement.')
        read({'path': path, 'sha256': delta['after_sha256']})
    require(changes[producer['path']]['after_sha256'] == producer['sha256'], 'Reviewed tail producer differs.')
    require(args.range_fix_projection_receipt and args.range_fix_projection_receipt_sha256,
            'Bind the separate native frame-precision projection; never retime inputs.')
    range_pin = read({'path': args.range_fix_projection_receipt, 'sha256': args.range_fix_projection_receipt_sha256})
    require(range_pin['sha256'] == '001311420fbe4f255ed18b4fe5fb69680f2f7048d0cbe9eaee8068f1f68e34bc',
            'Use the exact current one-function timing-admission projection.')
    range_fix = load(range_pin)
    admission = deepcopy(load(entry['continuation']['admission']))
    admission['range_fix_projection_receipt'] = range_pin
    # Shared pure stdlib checker only: never execute native run/admit/acceptance.
    import importlib.util
    checker_path = Path(TOOLS / 'run_character_composition_first_stage_continuation.py')
    spec = importlib.util.spec_from_file_location('composition_range_source_checker', checker_path)
    require(spec is not None and spec.loader is not None, 'Pinned SOURCE checker cannot be loaded.')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    require(checker.range_projection(admission) == range_fix, 'The paired range Source predicate differs.')

    def read_range_pins(value):
        if type(value) is dict:
            if {'path', 'sha256'} <= set(value):
                read(value)
            for child in value.values():
                read_range_pins(child)
        elif type(value) is list:
            for child in value:
                read_range_pins(child)

    read_range_pins(range_fix)
    for manifest_pin, count in [(row, count) for row, count in zip(range_fix['previous_projections'], (167, 173))] + [
            (range_fix['python_projection'], 167), (range_fix['complete_candidate'], 173)]:
        manifest = load(manifest_pin)
        package = Path(manifest_pin['path']).parent / 'character_designer'
        allowed_cache = set()
        if manifest_pin == range_fix['previous_projections'][0]:
            cache = range_fix['preserved_old_bytecode_cache']
            require(cache['count'] == len(cache['files']) == 116 and cache['executed'] is False
                    and cache['deleted'] is False and cache['copied'] is False, 'Old native bytecode is pinned history only.')
            for row in cache['files']:
                cache_path = Path(row['path']).resolve()
                require(cache_path.is_relative_to(package) and cache_path.parent.name == '__pycache__'
                        and cache_path.name == Path(row['source_leaf']).stem + '.cpython-313.pyc'
                        and row['source_leaf'] in manifest['files']
                        and row['source_sha256'] == manifest['files'][row['source_leaf']], 'Unproved historical cache/source association.')
                read(row)
                allowed_cache.add(cache_path.relative_to(package).as_posix())
            require(len(allowed_cache) == 116, 'Duplicate historical cache identity.')
        require(len(manifest['files']) == count and
                {path.relative_to(package).as_posix() for path in package.rglob('*') if path.is_file()} == set(manifest['files']) | allowed_cache,
                'Complete old/new projection source inventory changed.')
        for name, checksum in manifest['files'].items():
            read({'path': str(package / name), 'sha256': checksum})
    exporter_path = str(Path(range_fix['fix']['superseded_canonical_source_path']).resolve())
    exporter_before = range_fix['fix']['superseded_source']['sha256']
    exporter_after = range_fix['fix']['changed_runtime_sources']['animation_composition_export.py']['sha256']
    physical = old['physical_report']
    require(physical['completed'] is True and len(physical['items']) == 2
            and physical['worker_launch_performed'] is True
            and physical['physical_values_equal_foreground_and_two_a'] is True
            and physical['actual_fbx_and_metadata_hashes_verified'] is True, 'Require both actual physical Pose exports.')
    published = {}
    for index, item in enumerate(physical['items'], 1):
        require(item['completed'] is True and item['kind'] == 'SOURCE' and item['revision'] == 1
                and item['newActionId'] == '' and item['worker_launch_performed'] is True
                and item['actual_fbx_and_metadata_hashes_verified'] is True
                and item['physical_values_equal_independent_foreground'] is True, 'Never inherit an incomplete SOURCE export.')
        path = str(Path(item['manifestPath']).resolve())
        published[path] = item['manifest_after_sha256']
        manifest = load({'path': path, 'sha256': published[path]})
        require(manifest['revision'] == 1 and manifest['workItemId'] == item['publicManifestWorkItemId']
                and manifest['fbxSha256'] == item['fbx_sha256'] and manifest['metadataSha256'] == item['metadata_sha256'], 'Published Pose receipt changed.')
        for field in ('fbx', 'metadata'):
            read({'path': item[field + '_file'], 'sha256': item[field + '_sha256']})
        require(load({'path': str(ROOT / 'n12' / 'physical' / ('item_' + str(index)) / 'composition_sync_report.json'),
                      'sha256': digest(ROOT / 'n12' / 'physical' / ('item_' + str(index)) / 'composition_sync_report.json')}) == item,
                'Nested and standalone physical reports differ.')
    require(len(published) == 2 and {row['path']: row['sha256'] for row in failure['authorized_source_manifest_changes']} == published,
            'Only the two actual rev1 SOURCE manifests may change.')
    for path, expected in entry['source_inputs'].items():
        replacement = changes[path]['after_sha256'] if path in changes else published.get(path, expected)
        if Path(path).resolve() == Path(exporter_path):
            require(expected.lower() == exporter_before, 'The original product override is not exact runtime11 exporter history.')
            replacement = exporter_after
        read({'path': path, 'sha256': replacement})
    for record in failure['files']:
        read(record)
    latest_tool_before = {
        str(TOOLS / 'build_character_composition_continuation_fixture.py'): 'ec25dc21856d27d7b785fa58c2b78e8e8009ad96d68381c17b325a14d206f112',
        str(TOOLS / 'run_character_composition_first_stage_continuation.py'): 'af08e593e350646a0055cb689641631c08e111015a61219b713a45a14d441435',
        str(TOOLS / 'composition_timing_acceptance.py'): 'a89ffd1a3acf4937ef033bf4dfad1c77bc34ec883bc032f7dc0cf44e1bb99874'}
    require(set(latest_tool_before) == set(changes)
            and all(crash_entry['source_inputs'][path] == sha256 for path, sha256 in latest_tool_before.items())
            and crash_entry['runtime_projection'] == entry['runtime_projection']
            and crash_entry['full_projection'] == entry['full_projection'],
            'The short continuation must use the unchanged product and exact three reviewed SOURCE replacements.')
    for path, expected in crash_entry['source_inputs'].items():
        replacement = changes[path]['after_sha256'] if path in changes else expected
        if Path(path).resolve() == Path(exporter_path):
            require(expected.lower() == exporter_before, 'The latest product override is not exact runtime11 exporter history.')
            replacement = exporter_after
        read({'path': path, 'sha256': replacement})
    retained = {str(Path(row['path']).resolve()): row['sha256'].lower() for row in crash['inherited_two_source_publications']}
    physical_six = {str(Path(row['manifestPath']).resolve()): row['manifest_after_sha256'].lower() for row in physical['items']}
    physical_six.update({str(Path(row[field + '_file']).resolve()): row[field + '_sha256'].lower()
                        for row in physical['items'] for field in ('fbx', 'metadata')})
    require(crash['inherited_two_source_publications_unchanged'] is True and crash['inherited_two_source_revisions'] == [1, 1]
            and len(retained) == 6 and retained == physical_six, 'All six actual rev1 SOURCE publication bytes must remain exact.')
    sources = deepcopy(load(entry['tools_source_manifest'])['tools'])
    for name in ('run_character_composition_first_stage_continuation', 'composition_timing_acceptance'):
        path = str(TOOLS / (name + '.py'))
        sources[name] = read({'path': path, 'sha256': changes[path]['after_sha256']})
    frozen = {name: plan(destination / 'tools' / (name + '.py'), raw=Path(sources[name]['path']).read_bytes()) for name in entry['tools']}
    native = load(config['nativeFixture'])
    native.update(evidenceDirectory=str(destination),
                  addonPackage=str(Path(range_fix['python_projection']['path']).parent / 'character_designer'),
                  runtimeManifest=range_fix['python_projection']['path'], runtimeManifestSha256=range_fix['python_projection']['sha256'])
    config['nativeFixture'] = short(plan(destination / 'native_fixture.json', native))
    for key, directory, filename in [('physicalFixture', 'physical', 'physical_fixture.json'), ('loopAuthorFixture', 'loop_author', 'loop_author_fixture.json')]:
        value = load(config[key]); value['evidenceDirectory'] = str(destination / directory)
        config[key] = short(plan(destination / filename, value))
    config['tools'] = {name: short(frozen[name]) for name in config['tools']}
    config.update(loopSyncDirectory=str(destination / 'loop_sync'), qaSeedFile=str(destination / 'qa_seed.blend'))
    stage_pin = plan(destination / 'first_stage_config.json', config)
    admission['physical_tail'] = {'schema': 'x.validation.character-composition-physical-tail-reuse/2',
        **{name: read(row) for name, row in previous.items()}, 'owner_review': owner_pin, 'peer_review': peer_pin,
        **{name: read(row) for name, row in latest.items()}}
    admission_pin = plan(destination / 'reuse_admission.json', admission)
    bridge = deepcopy(load(entry['native_source_report']))
    bridge.update(source_files=list(sources.values()), owner_source_report=owner_pin, independent_source_review=peer_pin,
                  source_generation='Short actual Loop/native worker/QA seed continuation; inherited n12 fresh2 numerics and two-Pose publications; historical serializer proof plus separate native frame coverage admission only; default native bodies unchanged.')
    bridge_pin = plan(destination / 'native_source_report.json', bridge)
    manifest = deepcopy(load(entry['tools_source_manifest']))
    manifest.update(tools=sources, origin_manifest=entry['tools_source_manifest'], owner_source_report=owner_pin,
                    independent_source_review=peer_pin, range_fix_projection_receipt=range_pin,
                    delta={'updated': ['run_character_composition_first_stage_continuation', 'composition_timing_acceptance'], 'added': []})
    manifest_pin = plan(destination / 'tools_source_manifest.json', manifest)
    for path, expected in admitted.items():
        require(digest(path) == expected, 'Tail source changed during preparation: ' + path)
    entry.update(config=short(stage_pin), tools=frozen, source_builder=producer, native_source_report=bridge_pin,
                 runtime_projection=short(range_fix['python_projection']), full_projection=short(range_fix['complete_candidate']),
                 range_fix_projection_receipt=range_pin,
                 tools_source_manifest=manifest_pin, continuation={'entry': frozen['run_character_composition_first_stage_continuation'], 'admission': admission_pin},
                 origin_source_entry=read(previous['source_entry']), origin_first_stage_config=read(fixed('n12/first_stage_config.json', '2705b17944deedfb4999d9c6449b1e8421f0d7df9b36d26f2b33f4c8c90aa41e')),
                 source_inputs={**admitted, **{str(path): hashlib.sha256(raw).hexdigest() for path, raw in planned.items()}},
                 qaToolChange='Only remaining Loop/worker/seed; twelve numeric cases and actual n12 fresh2 inherited, zero fresh variant reexecution; two rev1 Pose publications preserved. Actual Pose Add occurs after Loop only for genuine UI seed rows.')
    plan(destination / 'source_entry_receipt.json', entry)
    require(len(frozen) == 7 and len(sources) == 11 and len(config['tools']) == 5 and native['sessionChecks'] is None
            and len(str(destination / 'DATAFILES').encode('utf-16-le')) // 2 + 174 < 260, 'Unexpected tail helper/profile scope.')
    if not args.write:
        return {'source_only': True, 'native_execution_performed': False, 'files_planned': len(planned), 'write_performed': False}
    destination.mkdir()
    for name in ('CONFIG', 'DATAFILES'):
        (destination / name).mkdir()
    for path, raw in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
    return {'source_only': True, 'native_execution_performed': False, 'config': stage_pin,
            'source_entry': {'path': str(destination / 'source_entry_receipt.json'), 'sha256': digest(destination / 'source_entry_receipt.json')}, 'continuation': entry['continuation']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('producer-sha256', 'continuation-peer', 'continuation-peer-sha256'):
        parser.add_argument('--' + field, required=True)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--tail', action='store_true')
    parser.add_argument('--tail-owner')
    parser.add_argument('--tail-owner-sha256')
    parser.add_argument('--range-fix-projection-receipt')
    parser.add_argument('--range-fix-projection-receipt-sha256')
    args = parser.parse_args()
    print(json.dumps((build_tail if args.tail else build)(args), allow_nan=False))


if __name__ == '__main__':
    main()
