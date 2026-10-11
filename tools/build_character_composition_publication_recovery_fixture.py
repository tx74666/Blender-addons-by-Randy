"""Freeze SOURCE-only fixtures for one bounded existing-publication recovery."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(r'D:\Blender\Projects\Character\X\validation\composition_20261011')
REPO = Path(__file__).resolve().parents[1]
UNITY = Path(r'D:\Unity Projects\RandomRealm2')
USTAGE = UNITY / '.codex/task-staging/character-composition-20261011'
DEST = ROOT / 'n16'
ENTRY = (ROOT / 'n15/source_entry_receipt.json', '111f933867cfd675b2204d250544518a21b507b106a1ec69020e85b1388bd534')
PROTECTION = (ROOT / 'n15_natural_failure_readback_5a2e5dd7.json', '17125fc4cf1f1a4e23549b028f1c0c59155d9d7b8764dca2738fed76895422e1')
PLAN = (ROOT / 'n15_published_loop_recovery_source_plan_compact_e1598ff1.json', 'b27c8e1c7cf09ec885b983d48688669aacb22d21fe1fda11697d0f46cd7b18cd')
UNITY_CLOSURE = (USTAGE / 'evidence/metadata-size-r19/closure.json', 'bd44a1352aa2fd14a04c8ddc6e18514d028f5df05e1677d9df7ef90a3e7c7eab')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def pin(path, expected):
    path = Path(path)
    require(path.is_absolute() and path.is_file() and re.fullmatch('[0-9a-f]{64}', expected), 'Require an exact absolute source pin.')
    require(sha(path) == expected, 'Source changed: ' + str(path))
    return {'path': str(path.resolve()), 'sha256': expected, 'bytes': path.stat().st_size}


def read(row):
    record = pin(row['path'], row['sha256'])
    require('bytes' not in row or row['bytes'] == record['bytes'], 'Source length changed.')
    return json.loads(Path(record['path']).read_text(encoding='utf-8'))


def encode(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')


def planned(path, value):
    raw = encode(value)
    return raw, {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def build(args):
    producer = pin(Path(__file__).resolve(), args.producer_sha256)
    driver = pin(REPO / 'tools/run_character_composition_publication_recovery.py', args.driver_sha256)
    reviews = [pin(args.driver_owner, args.driver_owner_sha256), pin(args.driver_peer, args.driver_peer_sha256)]
    for row in reviews:
        review = read(row)
        require(review.get('passed') is True and review.get('source_only') is True
                and review.get('native_execution_performed') is False, 'Require closed recovery SOURCE reviews.')
        reviewed_driver = review['source_files']['run_character_composition_publication_recovery.py']
        require(pin(reviewed_driver['path'], reviewed_driver['sha256']) == driver
                and reviewed_driver == driver, 'The review must bind these exact recovery driver bytes.')
    budget_pin = pin(ROOT / 'composition_animation_sidecar_budget_projection_receipt_13_14.json', args.budget_sha256)
    budget = read(budget_pin)
    require(budget['schema'] == 'x.validation.character-composition-animation-sidecar-budget-projections/1'
            and budget['source_only'] is True and budget['native_execution_performed'] is False
            and budget['byte_closure']['changed_product_paths'] == 7
            and budget['byte_closure']['unchanged_product_files'] == 333,
            'Require the exact separately reviewed seven-leaf sidecar projection.')
    old_entry_pin = pin(*ENTRY); old_entry = read(old_entry_pin)
    protection_pin = pin(*PROTECTION); protection = read(protection_pin)
    plan_pin = pin(*PLAN); plan = read(plan_pin)
    unity_closure_pin = pin(*UNITY_CLOSURE); unity_closure = read(unity_closure_pin)
    require(protection['completed'] is False and protection['native_process_natural_exit_code'] == 1
            and protection['source_inputs_unchanged_except_actual_partial_loop_publication'] is True
            and protection['full_outer_native_recovery_completed'] is False
            and unity_closure['passed'] is True and plan['source_only'] is True,
            'Use the actual failed attempt and separate receiver closure; no native success is inferred.')
    old_config = read(old_entry['config']); native = deepcopy(read(old_config['nativeFixture']))
    runtime_pin = pin(budget['python_projection']['path'], budget['python_projection']['sha256'])
    full_pin = pin(budget['complete_candidate']['path'], budget['complete_candidate']['sha256'])
    runtime, full = read(runtime_pin), read(full_pin)
    require(len(runtime['files']) == 167 and len(full['files']) == 173 and native['sessionChecks'] is None,
            'Keep the unchanged complete native fixture scope.')
    native.update(addonPackage=runtime['source_root'], runtimeManifest=runtime_pin['path'],
                  runtimeManifestSha256=runtime_pin['sha256'], evidenceDirectory=str(DEST))
    plans = []
    native_raw, native_pin = planned(DEST / 'native_fixture.json', native); plans.append((Path(native_pin['path']), native_raw))
    old_tools = old_entry['tools']
    require(len(old_tools) == 7, 'Keep the exact seven historical helper siblings.')
    frozen = {}
    for name, row in old_tools.items():
        source_pin = pin(row['path'], row['sha256']); raw = Path(source_pin['path']).read_bytes()
        path = DEST / 'tools' / Path(row['path']).name
        frozen[name] = {'path': str(path), 'sha256': source_pin['sha256'], 'bytes': len(raw)}
        plans.append((path, raw))
    driver_path = DEST / 'tools' / Path(driver['path']).name
    driver_raw = Path(driver['path']).read_bytes(); plans.append((driver_path, driver_raw))
    frozen_driver = {**driver, 'path': str(driver_path)}
    full_plan_pin = pin(ROOT / 'n15_published_loop_recovery_source_plan_e1598ff1.json', '5a773f6f4ce581b8692440b44a52f6d15dd16b186563b051107193192b59d1de')
    full_plan = read(full_plan_pin)
    evidence = full_plan['evidence']
    admission_pin = pin(ROOT / 'n15/reuse_admission.json', '84a22137470156814a49bf584596fc1dfa98363e9df05f979fa8f4087bc00eb6')
    admission = read(admission_pin)
    config = {'schema': 'x.validation.character-composition-publication-recovery-fixture/1', 'nativeFixture': native_pin,
        'failedSourceEntry': old_entry_pin, 'failedProtection': protection_pin,
        'loopAuthorReport': evidence['author_report'], 'workerJob': evidence['job'], 'workerSnapshot': evidence['archive'],
        'workerResult': evidence['worker_result'], 'publishedLink': evidence['published_manifest'],
        'inheritedPhysicalReport': admission['physical_tail']['physical_report'],
        'inheritedNumericReport': admission['numeric_report'], 'persistedNumericReport': admission['physical_tail']['native_report'],
        'sidecar_budget_projection_receipt': budget_pin,
        'tools': {name: frozen[name] for name in ('verify_character_composition_native', 'run_character_composition_first_stage_continuation')},
        'evidenceDirectory': str(DEST), 'qaSeedFile': str(DEST / 'qa_seed.blend')}
    config = {key: (pin(value['path'], value['sha256']) if isinstance(value, dict) and 'path' in value
                   and key != 'nativeFixture' else value) for key, value in config.items()}
    config_raw, config_pin = planned(DEST / 'recovery_config.json', config); plans.append((Path(config_pin['path']), config_raw))
    overrides = {str(Path(evidence['published_manifest']['path']).resolve()): evidence['published_manifest']}
    for field in ('changed_runtime_sources', 'changed_embedded_sources', 'changed_tool_sources'):
        for row in budget['fix'][field].values(): overrides[str(Path(row['path']).resolve())] = row
    current = {}; changed = []
    for path, before in old_entry['source_inputs'].items():
        resolved = str(Path(path).resolve()); expected = overrides.get(resolved, {'path': resolved, 'sha256': before})
        row = pin(expected['path'], expected['sha256']); current[resolved] = row['sha256']
        if before != row['sha256']: changed.append({'path': resolved, 'before_sha256': before, 'after_sha256': row['sha256']})
    require(len(old_entry['source_inputs']) == 1455, 'The protected failed admission inventory differs.')
    budget_proofs = [budget['fix'][name] for name in ('owner_source_report', 'CSharp_owner_source_report',
        'independent_source_review', 'focused_pure_test_report', 'focused_test_source', 'producer',
        'parent_range_fix_projection_receipt', 'failure_readback')]
    budget_proofs.extend(budget['fix']['receiver_actual_validation'][name] for name in ('closure', 'composition_contracts'))
    for row in [producer, driver, *reviews, budget_pin, plan_pin, full_plan_pin, admission_pin, old_entry_pin,
                protection_pin, unity_closure_pin, runtime_pin, full_pin, *budget_proofs, *evidence.values(), *overrides.values()]:
        checked = pin(row['path'], row['sha256']); current[checked['path']] = checked['sha256']
    for manifest in (runtime, full):
        for name, expected in manifest['files'].items():
            relative = Path(name); require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe projection leaf.')
            row = pin(Path(manifest['source_root']) / relative, expected); current[row['path']] = row['sha256']
    for path, raw in plans:
        current[str(path)] = hashlib.sha256(raw).hexdigest()
    source_entry = {'schema': 'x.validation.character-composition-publication-recovery-source-entry/1',
        'source_only': True, 'native_execution_performed': False, 'launch_permission_currently_pending': True,
        'config': config_pin, 'driver': frozen_driver, 'source_builder': producer, 'tools': frozen,
        'runtime_projection': runtime_pin, 'full_projection': full_pin, 'sidecar_budget_projection_receipt': budget_pin,
        'source_plan': plan_pin, 'driver_source_reviews': reviews, 'parent_receiver_closure': unity_closure_pin,
        'failed_admission_source_count': 1455, 'reviewed_current_overrides': changed, 'source_inputs': current,
        'expected_execution': {'published_SOURCE_inherited': 3, 'published_SOURCE_executed': 0, 'authoring_executed': 0,
            'numeric_resampling_executed': 0, 'current_receipt_acceptance': 1, 'current_UI_imports': 2,
            'current_Loop_imports': 1, 'fresh_outer_recovery_and_seed_required': True},
        'qa_seed_is_provisional_until_natural_exit_and_root_closure': True}
    entry_raw, entry_pin = planned(DEST / 'source_entry_receipt.json', source_entry); plans.append((Path(entry_pin['path']), entry_raw))
    require(not DEST.exists() and len({path for path, _raw in plans}) == len(plans), 'Keep all outputs fresh and exclusive.')
    require(all(path.is_relative_to(DEST) and not path.exists() for path, _raw in plans), 'Output ownership changed.')
    require(args.write, 'Explicit SOURCE-write intent required.')
    for path, raw in plans:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(raw)
    for path, raw in plans: require(path.read_bytes() == raw, 'Fresh fixture write differs.')
    print(json.dumps({'config': config_pin, 'source_entry': entry_pin, 'driver': frozen_driver,
                      'source_input_count': len(current), 'reviewed_overrides': changed}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('producer-sha256', 'driver-sha256', 'driver-owner', 'driver-owner-sha256', 'driver-peer', 'driver-peer-sha256', 'budget-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--write', action='store_true')
    build(parser.parse_args())


if __name__ == '__main__': main()
