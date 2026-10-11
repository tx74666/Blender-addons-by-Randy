"""Freeze eleven SOURCE-reviewed UI helpers; never run a native/UI acceptance.

Root must independently review this producer before invoking --write. Existing
headful08 and n14/n15 evidence remain immutable. This gives no execution permission.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(r'D:/Blender/Projects/Character/X/validation/composition_20261011')
OLD_RECEIPT = '1b0d106c99302ff66e0f262b695b868604127c507509760ab24de1b8455f0832'
SOURCE_MANIFEST = '4f12871b17cf042618d96e23cfb340939ebcf4764ff10e1843c16f0e4f5c82d7'
ENTRY = '111f933867cfd675b2204d250544518a21b507b106a1ec69020e85b1388bd534'
EDIT_A_REVIEW = '5b75fcad568a7ae630eac8efcab77a545bd3707d32f9467f79fd13f93c36d490'
INHERITED_CONTINUATION_REVIEW = 'a0bb2c6cec35240ed5da80f4d3a6e2761e057b6522565d37fa378266a607036c'
CONTINUATION_REVIEW = '8b132e81d91ed779a3819287eae35428af37cf641907f5a37daf11c68fc360cd'
OWNER_REVIEW = '46f1a3ac8739d9b674fbc70172a2e4b6c26f08ca8ac9c2c0cade1f530edb97bc'
NATIVE = '1ebf29f7ac4358884a3861164aa0e9894db37da57f74fe64a5ef3493b650ad32'
CONTINUATION = '23cda6830ce26421798df44b69b1c680b63536c393c9f22f16631f52818b411c'
TIMING = 'a89ffd1a3acf4937ef033bf4dfad1c77bc34ec883bc032f7dc0cf44e1bb99874'
OPERATOR_REVIEW = 'e5ca6468fe0594ef53762535b208ea9f8ff2c69e0943ab5a1090e387c13b7a47'


def require(value, message):
    if not value:
        raise ValueError(message)


def build(args):
    admitted, buffers = {}, {}

    def pin(path, expected):
        path = Path(path).resolve()
        require(path.is_file(), 'Missing exact SOURCE input.')
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected, 'Changed SOURCE pin: ' + str(path))
        admitted[path] = (expected, len(raw))
        return {'path': str(path), 'sha256': expected, 'bytes': len(raw)}, raw

    def load(row):
        checked, raw = pin(row['path'], row['sha256'])
        require('bytes' not in row or row['bytes'] == checked['bytes'], 'Changed source byte count.')
        value = json.loads(raw.decode('utf-8')); json.dumps(value, allow_nan=False)
        return value

    producer, _ = pin(Path(__file__).resolve(), args.producer_sha256)
    destination = ROOT / 'tools_headful_09'
    require(not destination.exists(), 'Never overwrite a frozen UI snapshot.')
    old_pin, _ = pin(ROOT / 'tools_headful_08/frozen_tools_receipt.json', OLD_RECEIPT)
    source_pin, _ = pin(ROOT / 'n15/tools_source_manifest.json', SOURCE_MANIFEST)
    entry_pin, _ = pin(ROOT / 'n15/source_entry_receipt.json', ENTRY)
    old, source, entry = load(old_pin), load(source_pin), load(entry_pin)
    require(source['source_only'] is True and source['native_execution_performed'] is False
            and entry['native_execution_performed'] is False and entry['tools_source_manifest'] == source_pin
            and len(old['files']) == len(source['tools']) == 11, 'Require actual n15 SOURCE and the immutable eleven-file origin.')
    inherited, prior_peer, peer_pin = old['inherited_source_review'], old['independent_source_review'], source['independent_source_review']
    require(inherited['sha256'] == EDIT_A_REVIEW and prior_peer['sha256'] == INHERITED_CONTINUATION_REVIEW
            and peer_pin['sha256'] == CONTINUATION_REVIEW
            and source['owner_source_report']['sha256'] == OWNER_REVIEW, 'Use exact inherited and current independent SOURCE reviews.')
    edit_a, prior, peer, owner = load(inherited), load(prior_peer), load(peer_pin), load(source['owner_source_report'])
    require(edit_a['source_only'] is True and edit_a['status'] == 'READY_SOURCE_ONLY_NATIVE_PENDING'
            and all(review['source_only'] is True and review['native_execution_performed'] is False
                    and review['passed'] is True for review in (prior, peer, owner))
            and owner['reviewed_source_changes'] == peer['reviewed_source_changes']
            and all(review['native_seam']['inverse_normalized_source_exact'] is True
                    and review['native_seam']['default_numeric_function_bodies_unchanged'] is True
                    for review in (owner, peer)), 'Inherited EditA and reviewed native-default/tail proofs are mandatory.')
    for review in (owner, peer):
        for row in review['source_files']:
            load_pin, _ = pin(row['path'], row['sha256'])
            require(load_pin['bytes'] == row['bytes'], 'Reviewed source byte count changed.')
    operator_pin = owner['operator_owner_source_report']
    require(operator_pin['sha256'] == OPERATOR_REVIEW
            and peer['operator_review']['owner_source_report'] == operator_pin
            and peer['operator_review']['no_visibility_hide_or_collection_writes'] is True
            and peer['operator_review']['literal_finished_and_positive_finite_rotation_delta_required'] is True
            and peer['operator_review']['background_undo_requested'] is False
            and peer['operator_review']['headful_undo_requested'] is True
            and peer['operator_review']['pre_native_pending_checkpoint_durable_before_C_call'] is True,
            'Require the exact bounded native operator SOURCE review; no native acceptance is implied.')
    operator = load(operator_pin)
    require(operator['source_only'] is True and operator['native_execution_performed'] is False,
            'The operator report must remain SOURCE-only.')
    changed = {'run_character_composition_first_stage_continuation'}
    require(set(source['tools']) == set(old['files'])
            and source['tools']['run_character_composition_first_stage_continuation']['sha256'] == CONTINUATION
            and source['tools']['composition_timing_acceptance']['sha256'] == TIMING
            and source['tools']['verify_character_composition_native']['sha256'] == NATIVE,
            'Only the exact single reviewed continuation update are admitted.')
    files = {}
    for name, row in source['tools'].items():
        checked, raw = pin(row['path'], row['sha256'])
        require(row['bytes'] == len(raw) and Path(row['path']).name == name + '.py', 'Exact source name/bytes required.')
        previous = old['files'][name]; pin(previous['path'], previous['sha256'])
        if name not in changed:
            require(previous['sha256'] == checked['sha256'], 'One of the ten inherited helpers changed.')
        else:
            require(previous['sha256'] != checked['sha256'], 'A declared tool update did not change.')
        path = destination / (name + '.py'); buffers[path] = raw
        files[name] = {**checked, 'path': str(path), 'canonical_source': checked['path']}
    for name, row in entry['tools'].items():
        require(name in files and row['sha256'] == files[name]['sha256'], 'New BG and UI helper pair differs.')
        pin(row['path'], row['sha256'])
    receipt = {'schema': 'x.validation.character-composition-tools-frozen/1', 'source_only': True,
        'native_execution_performed': False, 'native_pending': True, 'source_manifest': source_pin,
        'independent_source_review': peer_pin, 'inherited_source_review': inherited,
        'inherited_continuation_source_review': prior_peer, 'origin_frozen08_receipt': old_pin,
        'origin_frozen07_receipt': old['origin_frozen07_receipt'],
        'origin_frozen06_receipt': old['origin_frozen06_receipt'],
        'continuation_owner_source_report': source['owner_source_report'], 'operator_owner_source_report': operator_pin,
        'origin_source_entry': entry_pin,
        'delta': {'changed_tools': sorted(changed), 'added_tools': [], 'unchanged_count': 10},
        'producer': producer, 'files': files,
        'scope': 'SOURCE-only eleven helper snapshot. Ten files retain frozen08 bytes; the separately reviewed continuation admits the native precision range guard and preserves inherited numeric and two-SOURCE evidence. Inherited EditA/full-C proof remains exact. No new native, physical or UI acceptance implied.'}
    raw = (json.dumps(receipt, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    for path, (sha256, size) in admitted.items():
        current = path.read_bytes()
        require(len(current) == size and hashlib.sha256(current).hexdigest() == sha256, 'Input changed before exclusive creation.')
    if args.write:
        destination.mkdir()
        for path, content in buffers.items():
            with path.open('xb') as stream:
                stream.write(content)
        with (destination / 'frozen_tools_receipt.json').open('xb') as stream:
            stream.write(raw)
    return {'source_only': True, 'native_execution_performed': False, 'write_performed': args.write,
        'receipt': {'path': str(destination / 'frozen_tools_receipt.json'), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--producer-sha256', required=True)
    parser.add_argument('--write', action='store_true')
    print(json.dumps(build(parser.parse_args()), allow_nan=False))


if __name__ == '__main__':
    main()
