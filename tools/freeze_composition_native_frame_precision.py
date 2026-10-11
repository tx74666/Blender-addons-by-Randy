"""SOURCE-only exact one-function native frame-precision projection; no bpy or native work."""
import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(r'D:\Blender\Projects\Character\X\validation\composition_20261011')
REPO = Path(__file__).resolve().parents[1]
NAME = 'animation_composition_export.py'
SOURCE = REPO / 'addons/character_designer' / NAME
NEW_SHA = '562565879a9c4c05356f82c3b0ec45bd567d1311ef58cbde8a20607452079520'
BEFORE = '2dd2173ec604918328b25945f22916d96ce8039a322c7ed034ee65f686b69984'
PARENTS = ((ROOT / 'runtime_draft_11/python_projection.json', '5c1f5da5b46bf4f888f8ab226a86295775dae9020bd2af8d07f78e9336c1ae69'),
           (ROOT / 'full_candidate_12/candidate_projection.json', '4dd0fb1d45ba03b7723a899a7e3614a023120c8dd8e813e9678932e910194c94'))
DESTINATIONS = (ROOT / 'runtime_draft_12', ROOT / 'full_candidate_13')
RECEIPT = ROOT / 'composition_native_frame_precision_projection_receipt_12_13.json'
SERIALIZER = (ROOT / 'composition_snapshot_serializer_projection_receipt_11_12.json', 'cbf7528f2c014389ba3b4d29adc359e7dd89ec25e460b5cf51674f34ef8145cb')
PROTECTION = (ROOT / 'n14_natural_failure_readback_43b9cf86.json', '829c2758f1e7b33e52ed46571891a55d2dc31313a9816fc9cb951f607e459488')
PROJECTION_TOOL_SHA = '052e6c765c95ba46d480bf1eeca1e1dccb7685c77a4e756202ce409d20713106'
PARENT_REVIEW = Path(r'D:\Unity Projects\RandomRealm2\.codex\task-staging\character-composition-20261011\evidence\x-n15-native-frame-precision-source-review')
OWNER = (PARENT_REVIEW / 'implementation.json', '6b5a18d81afd3943795504449183f2a650361a1c64f85b183ca5ca027a968954')
PEER = (PARENT_REVIEW / 'peer.json', '60b2d7afa60ec8cb9a90b157fbfaba960b24b91f4cbf90e567a35d124adba829')

def require(value, message):
    if not value:
        raise ValueError(message)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def read_pin(path, expected):
    path = Path(path)
    require(path.is_absolute() and path.is_file() and re.fullmatch('[0-9a-f]{64}', expected), 'Require exact absolute source pin.')
    raw = path.read_bytes()
    require(digest(raw) == expected, 'Changed source pin: ' + str(path))
    return raw, {'path': str(path.resolve()), 'sha256': expected, 'bytes': len(raw)}

def write_json(path, value):
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    with path.open('xb') as stream:
        stream.write(raw)
    return {'path': str(path), 'sha256': digest(raw), 'bytes': len(raw)}

def build(producer_sha256):
    _, producer = read_pin(Path(__file__).resolve(), producer_sha256)
    new, source = read_pin(SOURCE, NEW_SHA)
    _, helper = read_pin(REPO / 'tools/release_projection.py', PROJECTION_TOOL_SHA)
    from release_projection import load_source
    parents, snapshots, parent_pins, caches = [], [], [], []
    for index, (path, expected) in enumerate(PARENTS):
        raw, pin = read_pin(path, expected); parent = json.loads(raw); frozen = load_source('character_designer', path)
        require(frozen.version == '0.77.25' and len(frozen.files) == (167 if index == 0 else 173)
                and parent['files'][NAME] == BEFORE, 'Unexpected immutable parent source inventory.')
        root = Path(parent['source_root']); extras = [p for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix() not in parent['files']]
        if index == 0:
            require(len(extras) == 116, 'Only the observed old116 native bytecode cache is admitted.')
            for cache in sorted(extras):
                relative = cache.relative_to(root)
                require(len(relative.parts) == 2 and relative.parts[0] == '__pycache__'
                        and relative.name.endswith('.cpython-313.pyc')
                        and relative.name[:-len('.cpython-313.pyc')] + '.py' in parent['files']
                        and not cache.is_symlink() and not cache.parent.is_symlink(), 'Unexpected old projection extra.')
                cache_raw = cache.read_bytes()
                caches.append({'path': str(cache), 'sha256': digest(cache_raw), 'bytes': len(cache_raw),
                    'source_leaf': relative.name[:-len('.cpython-313.pyc')] + '.py',
                    'source_sha256': parent['files'][relative.name[:-len('.cpython-313.pyc')] + '.py']})
        else:
            require(not extras, 'The full parent must retain its exact raw173 files.')
        parents.append(parent); snapshots.append(frozen); parent_pins.append(pin)
    old = snapshots[0].files[Path(NAME)]
    require(old == snapshots[1].files[Path(NAME)], 'Both parents must share exact exporter bytes.')
    before_text, after_text = old.decode('utf-8'), new.decode('utf-8')
    a, b = ast.parse(before_text), ast.parse(after_text)
    funcs = lambda tree: {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    af, bf = funcs(a), funcs(b)
    require(set(af) == set(bf) and [name for name in af if af[name] != bf[name]] == ['_validate_action_timing'], 'Only native timing admission may change.')
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
    normalized = after_text.replace('\r\n', '\n')
    require(normalized.count(replacement) == 1, 'Require exact reviewed native float32 admission block.')
    inverse = normalized.replace(replacement, '    requested_first, requested_last = _frame_range(spec)')
    require(ast.dump(ast.parse(inverse), include_attributes=False) == ast.dump(a, include_attributes=False), 'Other module functions or top-level code changed.')
    owner_raw, owner_pin = read_pin(*OWNER); peer_raw, peer_pin = read_pin(*PEER)
    owner, peer = json.loads(owner_raw), json.loads(peer_raw)
    require(owner['schema'] == 'randomrealm.character-composition-native-frame-precision-source-review/1'
            and peer['schema'] == 'randomrealm.character-composition-native-frame-precision-peer-source-review/1'
            and owner['passed'] is True and peer['passed'] is True
            and owner['nativeExecutionPerformed'] is False and peer['nativeExecutionPerformed'] is False
            and owner['changedFunctions'] == peer['independentComparison']['changedFunctions'] == ['_validate_action_timing']
            and owner['sourceInverseExact'] is True and peer['independentComparison']['inverseNormalizedSourceTextExact'] is True
            and peer['independentComparison']['wholeModuleAstExactExceptTimingFunction'] is True
            and peer['independentComparison']['addedFunctions'] == peer['independentComparison']['removedFunctions'] == [],
            'Require the actual paired single-function SOURCE reviews.')
    for record, actual in ((owner['productChange'], source), (owner['baseline'], {'path': str(Path(parents[0]['source_root']) / NAME), 'sha256': BEFORE, 'bytes': len(old)})):
        require(Path(record['path']).resolve() == Path(actual['path']).resolve()
                and record['sha256'].lower() == actual['sha256'] and record['bytes'] == actual['bytes'], 'Parent reviews admit different source bytes.')
    require(type(owner['verification']['tests']) is int and owner['verification']['tests'] == owner['verification']['passed'] == 38,
            'Require the actual positive38 focused-test result.')
    _, test_pin = read_pin(owner['tests']['path'], owner['tests']['sha256'].lower())
    require(test_pin['bytes'] == owner['tests']['bytes'], 'Focused test source bytes differ.')
    for record in peer['actualPins']:
        read_pin(record['path'], record['sha256'].lower())
    _, serializer = read_pin(*SERIALIZER); _, protection = read_pin(*PROTECTION)
    info = parents[1]['source_provenance']
    require(info['python_projection'] == str(PARENTS[0][0]) and info['python_projection_sha256'] == PARENTS[0][1], 'Full parent Python pairing differs.')
    embedded_raw, embedded = read_pin(info['embedded_overlay_manifest'], info['embedded_overlay_manifest_sha256'])
    require(embedded['sha256'] == 'c3e7742ac5068f2aafb7f9937a59a866bad7f719c92a3bcb7a45154e7d73e85c'
            and len(json.loads(embedded_raw)['files']) == len(info['canonical_embedded_overlays']) == 8, 'Retain exactly historical embeddedr6 overlays.')
    require(all(not p.exists() for p in DESTINATIONS) and not RECEIPT.exists(), 'Never overwrite a projection or receipt.')
    # All admitted source bytes are rehashed before the first exclusive write.
    for parent, frozen, pin in zip(parents, snapshots, parent_pins):
        read_pin(pin['path'], pin['sha256'])
        for name, raw in frozen.files.items():
            require(read_pin(Path(parent['source_root']) / name, parent['files'][name.as_posix()])[0] == raw, 'Old leaf changed before write.')
    for pin in [source, helper, producer, serializer, protection, embedded, owner_pin, peer_pin, test_pin, *caches]:
        read_pin(pin['path'], pin['sha256'])
    superseded = {'path': str(Path(parents[0]['source_root']) / NAME), 'sha256': BEFORE, 'bytes': len(old)}
    proof = {'normalized_full_module_inverse_ast_equal': True, 'changed_functions': [NAME + ':_validate_action_timing'],
        'unchanged_existing_function_count': len(af)-1, 'added_function_count': 0, 'top_level_ast_unchanged': True,
        'matrix_curve_sample_hash_isolation_worker_unchanged': True, 'sealed_seconds_and_sample_interval_unchanged': True}
    fix = {'kind': 'Native float32 FCurve coverage admission; sealed seconds and exact sampling preserved',
        'changed_runtime_sources': {NAME: source}, 'before_sha256': {NAME: BEFORE},
        'superseded_source': superseded, 'superseded_canonical_source_path': str(SOURCE),
        'changed_functions': {NAME: ['_validate_action_timing']}, 'added_functions': {NAME: []},
        'independent_ast_normalization': proof, 'parent_serializer_projection_receipt': serializer,
        'failure_readback': protection, 'owner_source_report': owner_pin, 'independent_source_review': peer_pin,
        'focused_pure_test_report': owner_pin, 'focused_test_source': test_pin, 'owner_reported_focused_tests_passed': 38,
        'producer': producer, 'native_validation': 'pending'}
    outputs, all_diff = [], []
    for index, (parent, frozen, destination) in enumerate(zip(parents, snapshots, DESTINATIONS)):
        destination.mkdir(exist_ok=False); package = destination / 'character_designer'; package.mkdir(exist_ok=False)
        for relative, raw in frozen.files.items():
            path = package / relative; path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(new if relative.as_posix() == NAME else raw)
        manifest = deepcopy(parent); manifest['source_root'] = str(package); manifest['files'][NAME] = NEW_SHA
        provenance = manifest['source_provenance']; provenance.update(native_validation='pending', complete_release=False,
            previous_projection=parent_pins[index]['path'], previous_projection_sha256=parent_pins[index]['sha256'], native_frame_precision=fix)
        if index == 0:
            provenance['canonical_python_overlays'][NAME] = {'canonical_source': source['path'], 'sha256': NEW_SHA}
        else:
            provenance.update(python_projection=outputs[0]['path'], python_projection_sha256=outputs[0]['sha256'])
        output = write_json(destination / ('python_projection.json' if index == 0 else 'candidate_projection.json'), manifest)
        verified = load_source('character_designer', output['path'])
        require(set(verified.files) == set(frozen.files), 'Projection file inventory changed.')
        differences = [name.as_posix() for name in frozen.files if frozen.files[name] != verified.files[name]]
        require(differences == [NAME], 'A product file other than exact exporter changed.')
        raw_paths = {p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
        require(raw_paths == set(manifest['files']), 'A new projection contains cache or extra files.')
        outputs.append({**output, 'file_count': len(verified.files), 'changed_product_files': differences})
        all_diff.append({'previous_projection': parent_pins[index], 'projection': output,
            'changed_relative_paths': differences, 'unchanged_file_count': len(verified.files)-1})
    for pin in [*parent_pins, source, *caches]:
        read_pin(pin['path'], pin['sha256'])
    receipt = write_json(RECEIPT, {'schema': 'x.validation.character-composition-native-frame-precision-projections/1',
        'source_only': True, 'native_execution_performed': False, 'release_or_deployment_performed': False,
        'fix': fix, 'previous_projections': parent_pins, 'python_projection': outputs[0], 'complete_candidate': outputs[1],
        'embedded_overlay_manifest': embedded, 'embedded_overlay_count': 8, 'embedded_overlays_unchanged': True,
        'byte_closure': {'old_product_files': 340, 'new_product_files': 340, 'changed_exporter_paths': 2,
            'unchanged_product_files': 338, 'projections': all_diff},
        'preserved_old_bytecode_cache': {'count': len(caches), 'files': caches, 'executed': False, 'deleted': False, 'copied': False},
        'scope': 'Source projection only; historicalr6 embedded bytes retained, not a final current Unity release claim.'})
    print(json.dumps({'projections': outputs, 'receipt': receipt}, indent=2))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--producer-sha256', required=True)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    require(args.write is True, 'Explicit source-write intent required.')
    build(args.producer_sha256)

if __name__ == '__main__':
    main()
