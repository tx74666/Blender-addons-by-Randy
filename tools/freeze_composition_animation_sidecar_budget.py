"""SOURCE-only 32 MiB sidecar receiver projection; no native work or publication."""
import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(r'D:\Blender\Projects\Character\X\validation\composition_20261011')
REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / 'addons/character_designer'
USTAGE = Path(r'D:\Unity Projects\RandomRealm2\.codex\task-staging\character-composition-20261011')
PARENTS = ((ROOT / 'runtime_draft_12/python_projection.json', 'c1dc22a65fb20a5372972337282cd78be195cb0cfec0382075390506fd43e53c'),
           (ROOT / 'full_candidate_13/candidate_projection.json', '172ee6cf99167d3e23a620dd56bd0219a51dfa0e0f39686c9be1646399cc7cf1'))
DESTINATIONS = (ROOT / 'runtime_draft_13', ROOT / 'full_candidate_14')
RECEIPT = ROOT / 'composition_animation_sidecar_budget_projection_receipt_13_14.json'
PYTHON = {
    'animation_link.py': ('aabd94b5ce0b2cac63ed88d1f24fab35636e0eb14aacd07de105d7e84ef3fccb', '05a981398b7c4edca7e43de1ac8e7ae8fbabb803dda2e05bed877803bab6f83c'),
    'animation_worklist_collection.py': ('7e306c7af9e3b55aacd84ca5b16a44d3980b459ca905a07111c99132b6b9968f', 'e0a0114f9c990bbc9687f982a445e384bf23fd35abc77c24ee7787456638a324'),
    'animation_worklist_new.py': ('b332692c2119c369916a126ec689b5dbdf68c63c7534f54a07e12bab142a7c9d', 'fa0396f50879f8312442502438286494200986760ec82119fee8deb9b286bbc1')}
RETURN = 'unity_runtime/Editor/CharacterAnimationReturn.cs'
RETURN_BEFORE = '85e6232cf5652e96629fdbcff192a1156503a092453826a4a0f5a6818aabe1a5'
RETURN_AFTER = '26f13c7191756eacfc189ed728eebbc7c580fb0ec1b4cbab23115c73bf1f143b'
RUNTIME_RETURN = '4b5ceb26d45e90b77af10d7bf80024190a3e0108c257b388e0e29421cda02285'
EXPORTER = '562565879a9c4c05356f82c3b0ec45bd567d1311ef58cbde8a20607452079520'
OWNER = (ROOT / 'animation_sidecar32_owner_source_e0a0114f.json', 'e68a079e01172e756ebddf4e81d10d6e0c0d8890da784187a5eae565d23d2b8e')
CS_OWNER = (ROOT / 'character_animation_return_metadata_32mib_source_owner_26f13c71.json', '95811734cf126a5f80be56772298a58c9d2c93c559172915bd916701d7a573f8')
PEER = (ROOT / 'animation_sidecar32_peer_source_review_e0a0114f_26f13c71.json', 'd8ca616b8a7b196639cbb38392d79f97428e94914b92ca495df1ef3d2435cc31')
RANGE = (ROOT / 'composition_native_frame_precision_projection_receipt_12_13.json', '001311420fbe4f255ed18b4fe5fb69680f2f7048d0cbe9eaee8068f1f68e34bc')
PROTECTION = (ROOT / 'n15_natural_failure_readback_5a2e5dd7.json', '17125fc4cf1f1a4e23549b028f1c0c59155d9d7b8764dca2738fed76895422e1')
CLOSURE = (USTAGE / 'evidence/metadata-size-r19/closure.json', 'bd44a1352aa2fd14a04c8ddc6e18514d028f5df05e1677d9df7ef90a3e7c7eab')
CONTRACTS = (USTAGE / 'evidence/metadata-size-r19/composition-contracts.json', '01bdd008ce83b56b65997e500c070fbde599081ed0b905549c7d190167290948')
PROJECTION_TOOL_SHA = '052e6c765c95ba46d480bf1eeca1e1dccb7685c77a4e756202ce409d20713106'

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

def encoded(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')

def write_json(path, value):
    raw = encoded(value)
    with path.open('xb') as stream:
        stream.write(raw)
    return {'path': str(path), 'sha256': digest(raw), 'bytes': len(raw)}

def nonreparse(path):
    return not path.is_symlink() and not (getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)

def snapshot(parent, index):
    root = Path(parent['source_root'])
    require(root == PARENTS[index][0].parent / 'character_designer' and root.is_dir() and nonreparse(root), 'Unexpected immutable source root.')
    require(parent['module'] == 'character_designer' and parent['version'] == '0.77.25'
            and len(parent['files']) == (167 if index == 0 else 173), 'Unexpected parent inventory or version.')
    paths = list(root.rglob('*'))
    require(all(nonreparse(p) for p in paths), 'Projection contains a reparse entry.')
    actual = {p.relative_to(root).as_posix(): p for p in paths if p.is_file()}
    require(set(parent['files']) <= set(actual), 'An immutable source leaf is missing.')
    caches = []
    for name in sorted(set(actual) - set(parent['files'])):
        relative = Path(name)
        require(index == 0 and len(relative.parts) == 2 and relative.parts[0] == '__pycache__'
                and relative.name.endswith('.cpython-313.pyc'), 'Unexpected old projection extra.')
        source_leaf = relative.name[:-len('.cpython-313.pyc')] + '.py'
        require(source_leaf in parent['files'], 'Old bytecode has no pinned source leaf.')
        raw = actual[name].read_bytes()
        caches.append({'path': str(actual[name]), 'sha256': digest(raw), 'bytes': len(raw),
                       'source_leaf': source_leaf, 'source_sha256': parent['files'][source_leaf]})
    files = {}
    for name, expected in parent['files'].items():
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe source leaf.')
        files[name] = read_pin(actual[name], expected)[0]
    return files, caches

def inverse_python(name, before, after):
    text = after.decode('utf-8').replace('\r\n', '\n')
    if name == 'animation_link.py':
        new = '# Animation sidecars carry complete numeric samples; Link manifests remain 1 MiB.\nANIMATION_METADATA_MAX_BYTES = 32 * 1024 * 1024\n'
        require(text.count(new) == 1, 'Unexpected shared metadata budget declaration.')
        inverse = text.replace(new, '')
    else:
        prefix = 'The published composition metadata' if 'collection' in name else 'The new Action metadata'
        require(text.count('> links.ANIMATION_METADATA_MAX_BYTES:') == 1 and text.count(prefix + ' exceeds 32 MiB.') == 1, 'Unexpected sidecar reader delta.')
        inverse = text.replace('> links.ANIMATION_METADATA_MAX_BYTES:', '> 1024 * 1024:').replace(prefix + ' exceeds 32 MiB.', prefix + ' exceeds 1 MiB.')
    before_text = before.decode('utf-8').replace('\r\n', '\n')
    require(inverse == before_text and ast.dump(ast.parse(inverse), include_attributes=False) == ast.dump(ast.parse(before_text), include_attributes=False), 'Other Python code changed: ' + name)
    return {'normalized_whole_source_inverse_exact': True, 'normalized_full_module_inverse_ast_equal': True,
            'sampling_curves_hashes_slots_and_export_unchanged': True}

def inverse_return(before, after):
    declaration = b'        // Full sampled composition evidence is larger than its small Link manifest.\n        public const int MaxMetadataBytes = 32 * 1024 * 1024;\n'
    pairs = ((declaration, b''),
        (b'Require(new FileInfo(path).Length <= MaxMetadataBytes, "The animation export metadata exceeds 32 MiB.");', b'Require(new FileInfo(path).Length <= 1024 * 1024, "The animation export metadata is unexpectedly large.");'),
        (b'Require(metadataBytes.Length <= MaxMetadataBytes, "The animation export metadata grew beyond 32 MiB while being read.");', b'Require(metadataBytes.Length <= 1024 * 1024, "The animation export metadata grew while being read.");'))
    inverse = after
    for new, old in pairs:
        require(inverse.count(new) == 1, 'Unexpected Return metadata reader delta.')
        inverse = inverse.replace(new, old)
    require(inverse == before, 'Other Return source bytes changed.')
    return {'whole_source_inverse_byte_exact': True, 'strict_UTF8_BOM_original_byte_SHA_FBX_SHA_and_other_contracts_unchanged': True}

def build(producer_sha256):
    _, producer = read_pin(Path(__file__).resolve(), producer_sha256)
    _, helper = read_pin(REPO / 'tools/release_projection.py', PROJECTION_TOOL_SHA)
    parents, files, parent_pins, caches = [], [], [], []
    for index, arguments in enumerate(PARENTS):
        raw, pin = read_pin(*arguments); parent = json.loads(raw)
        held, observed = snapshot(parent, index)
        parents.append(parent); files.append(held); parent_pins.append(pin); caches.extend(observed)
    require(files[0]['animation_composition_export.py'] == files[1]['animation_composition_export.py']
            and digest(files[0]['animation_composition_export.py']) == EXPORTER, 'Exporter must remain exact562.')
    _, exporter_pin = read_pin(PACKAGE / 'animation_composition_export.py', EXPORTER)
    require(parents[0]['files'][RETURN] == RUNTIME_RETURN and parents[1]['files'][RETURN] == RETURN_BEFORE, 'Distinct historical Return baselines changed.')
    replacements, source_pins, inverse_proof = {}, {}, {}
    for name, (before, after) in PYTHON.items():
        require(parents[0]['files'][name] == parents[1]['files'][name] == before and files[0][name] == files[1][name], 'Receiver baselines differ.')
        replacements[name], source_pins[name] = read_pin(PACKAGE / name, after)
        inverse_proof[name] = inverse_python(name, files[0][name], replacements[name])
    return_raw, return_pin = read_pin(PACKAGE / RETURN, RETURN_AFTER)
    inverse_proof[RETURN] = inverse_return(files[1][RETURN], return_raw)
    owner_raw, owner_pin = read_pin(*OWNER); cs_raw, cs_pin = read_pin(*CS_OWNER); peer_raw, peer_pin = read_pin(*PEER)
    owner, cs_owner, peer = map(json.loads, (owner_raw, cs_raw, peer_raw))
    require(all(d.get('source_only') is True and d.get('passed') is True and d.get('native_execution_performed') is False for d in (owner, cs_owner, peer)), 'Require paired SOURCE reviews.')
    require(owner['changed_runtime_sources'] == peer['changed_runtime_sources'] == source_pins
            and cs_owner['canonical_source'] == return_pin and cs_owner['whole_source_inverse_byte_exact'] is True,
            'Review source maps differ from current exact bytes.')
    require(peer['changed_embedded_sources'] == {RETURN: return_pin}, 'Peer embedded source map differs.')
    parent_return, parent_return_pin = read_pin(cs_owner['parent_unity_source']['path'], RETURN_AFTER)
    require(parent_return == return_raw and parent_return_pin == cs_owner['parent_unity_source'], 'Parent Return bytes differ from reviewed canonical source.')
    require(owner['focused_tests']['count'] == owner['focused_tests']['passed'] == 10 and owner['focused_tests']['exit_code'] == 0, 'Require actual owner10 pure tests.')
    _, test_pin = read_pin(owner['test_source']['path'], owner['test_source']['sha256'])
    require(test_pin == owner['test_source'], 'Focused test source changed.')
    sync_record = owner['changed_tool_sources']['composition_sync_acceptance.py']
    _, sync_pin = read_pin(sync_record['path'], sync_record['sha256'])
    require(sync_pin == sync_record and peer['changed_tool_sources'] == owner['changed_tool_sources'], 'Diagnostic tool source changed.')
    _, range_pin = read_pin(*RANGE); _, protection_pin = read_pin(*PROTECTION)
    closure_raw, closure_pin = read_pin(*CLOSURE); contracts_raw, contracts_pin = read_pin(*CONTRACTS)
    closure, contracts = json.loads(closure_raw), json.loads(contracts_raw)
    require(closure['passed'] is True and closure['sourcePinsUnchanged'] is True
            and closure['compositionContractsSha256'].lower() == contracts_pin['sha256'] and contracts['passed'] is True, 'Parent receiver validation changed or failed.')
    info = parents[1]['source_provenance']
    require(info['python_projection'] == parent_pins[0]['path'] and info['python_projection_sha256'] == parent_pins[0]['sha256'], 'Full parent Python pairing differs.')
    embedded_raw, embedded_pin = read_pin(info['embedded_overlay_manifest'], info['embedded_overlay_manifest_sha256'])
    require(embedded_pin['sha256'] == 'c3e7742ac5068f2aafb7f9937a59a866bad7f719c92a3bcb7a45154e7d73e85c'
            and len(json.loads(embedded_raw)['files']) == len(info['canonical_embedded_overlays']) == 8, 'Unexpected historical embedded overlay set.')
    require(all(parents[1]['files'][name] == record['sha256'] for name, record in info['canonical_embedded_overlays'].items()), 'Historical embedded overlay bytes differ.')
    require(all(not p.exists() for p in DESTINATIONS) and not RECEIPT.exists(), 'Never overwrite projections or receipt.')
    # Old340 leaf bytes were each read and hashed once; copies use those held bytes.
    # Only source/review pins are rechecked here. Bytecode is never imported or copied.
    for pin in [producer, helper, *source_pins.values(), return_pin, parent_return_pin, exporter_pin, owner_pin, cs_pin, peer_pin, test_pin, sync_pin, range_pin, protection_pin, closure_pin, contracts_pin, embedded_pin, *parent_pins, *caches]:
        read_pin(pin['path'], pin['sha256'])
    fix = {'kind': 'Bounded 32 MiB animation-sidecar readers; small manifests remain 1 MiB',
        'changed_runtime_sources': source_pins, 'changed_embedded_sources': {RETURN: return_pin},
        'changed_tool_sources': {'composition_sync_acceptance.py': sync_pin},
        'unchanged_exporter_source': exporter_pin,
        'before_sha256': {**{n: pair[0] for n, pair in PYTHON.items()}, RETURN: RETURN_BEFORE},
        'changed_functions': {'animation_link.py': [], 'animation_worklist_collection.py': ['_published_composition'], 'animation_worklist_new.py': ['publish'], RETURN: ['ReadMetadata']},
        'independent_inverse_proof': inverse_proof, 'parent_range_fix_projection_receipt': range_pin,
        'failure_readback': protection_pin, 'owner_source_report': owner_pin, 'CSharp_owner_source_report': cs_pin,
        'independent_source_review': peer_pin, 'focused_pure_test_report': owner_pin, 'focused_test_source': test_pin,
        'owner_reported_focused_tests_passed': 10, 'producer': producer, 'native_validation': 'pending',
        'receiver_actual_validation': {'closure': closure_pin, 'composition_contracts': contracts_pin,
            'scope': 'Parent isolated Unity readers/contracts only; no Loop Collect, recovery, UI or Apply claim'}}
    from release_projection import load_source
    outputs, differences, plans = [], [], []
    for index, (parent, held, destination) in enumerate(zip(parents, files, DESTINATIONS)):
        expected_changes = sorted([*PYTHON, *([RETURN] if index else [])])
        changes = {**replacements, **({RETURN: return_raw} if index else {})}
        manifest = deepcopy(parent); manifest['source_root'] = str(destination / 'character_designer')
        manifest['files'].update({name: digest(raw) for name, raw in changes.items()})
        provenance = manifest['source_provenance']; provenance.update(native_validation='pending', complete_release=False,
            previous_projection=parent_pins[index]['path'], previous_projection_sha256=parent_pins[index]['sha256'], animation_sidecar_budget=fix)
        if index == 0:
            provenance['canonical_python_overlays'].update({name: {'canonical_source': pin['path'], 'sha256': pin['sha256']} for name, pin in source_pins.items()})
        else:
            provenance.update(python_projection=outputs[0]['path'], python_projection_sha256=outputs[0]['sha256'],
                embedded_overlay_scope='Historical r6 seven leaves retained; Return receives the explicit reviewed 32 MiB overlay.')
            provenance['canonical_embedded_overlays'][RETURN] = {'canonical_source': return_pin['path'], 'sha256': RETURN_AFTER}
        manifest_raw = encoded(manifest)
        manifest_path = destination / ('python_projection.json' if index == 0 else 'candidate_projection.json')
        output = {'path': str(manifest_path), 'sha256': digest(manifest_raw), 'bytes': len(manifest_raw)}
        outputs.append({**output, 'file_count': len(held), 'changed_product_files': expected_changes})
        differences.append({'previous_projection': parent_pins[index], 'projection': output, 'changed_relative_paths': expected_changes, 'unchanged_file_count': len(held)-len(expected_changes)})
        plans.append((held, destination, changes, manifest, manifest_raw, manifest_path, expected_changes, output))
    # Both manifests are serializable and have fixed raw pins before any creation.
    for held, destination, changes, manifest, manifest_raw, manifest_path, expected_changes, output in plans:
        destination.mkdir(exist_ok=False); package = destination / 'character_designer'; package.mkdir(exist_ok=False)
        for relative, raw in held.items():
            path = package / relative; path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(changes.get(relative, raw))
        with manifest_path.open('xb') as stream:
            stream.write(manifest_raw)
        verified = load_source('character_designer', output['path'])
        actual_changes = sorted(name.as_posix() for name, raw in verified.files.items() if raw != held[name.as_posix()])
        require(set(p.as_posix() for p in verified.files) == set(held) and actual_changes == expected_changes, 'Unexpected new projection delta.')
        require({p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()} == set(held), 'New projection contains extra files.')
    receipt = write_json(RECEIPT, {'schema': 'x.validation.character-composition-animation-sidecar-budget-projections/1',
        'source_only': True, 'native_execution_performed': False, 'release_or_deployment_performed': False,
        'fix': fix, 'previous_projections': parent_pins, 'python_projection': outputs[0], 'complete_candidate': outputs[1],
        'historical_embedded_overlay_manifest': embedded_pin, 'historical_embedded_overlays_retained_count': 7,
        'current_embedded_budget_overlay': {RETURN: return_pin}, 'runtime_historical_Return_sha256_retained': RUNTIME_RETURN,
        'byte_closure': {'old_product_files': 340, 'new_product_files': 340, 'changed_product_paths': 7, 'unchanged_product_files': 333, 'projections': differences},
        'preserved_old_bytecode_cache': {'count': len(caches), 'files': caches, 'executed': False, 'deleted': False, 'copied': False},
        'scope': 'SOURCE projection only; exporter562 and all unrelated files retained. Collection receipt recovery, current UI rows/seed and native acceptance remain pending.'})
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
