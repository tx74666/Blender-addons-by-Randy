"""SOURCE-only one-file serializer freeze; invoke only after exact owner/peer/test pins."""
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
BEFORE = '82298a4a1a9a03f2d36bea52a79e41959f99d3ffd83bc17d81326272d08d3a67'
PARENTS = ((ROOT / 'runtime_draft_10/python_projection.json',
            'a7c660d546bcc6f2b0720750eb03279b62a24f968967a385746c6f13effeb150'),
           (ROOT / 'full_candidate_11/candidate_projection.json',
            '12ee822739fe467db86eee44f861f70d006d219957bd249f8d179e41d2415d7d'))
DESTINATIONS = (ROOT / 'runtime_draft_11', ROOT / 'full_candidate_12')
RECEIPT = ROOT / 'composition_snapshot_serializer_projection_receipt_11_12.json'
PROJECTION_TOOL_SHA = '052e6c765c95ba46d480bf1eeca1e1dccb7685c77a4e756202ce409d20713106'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_pin(path, checksum):
    path = Path(path)
    require(path.is_absolute() and path.is_file() and re.fullmatch('[0-9a-f]{64}', checksum), 'Require an absolute file and full lowercase SHA.')
    raw = path.read_bytes()
    require(digest(raw) == checksum, 'Changed pinned input: ' + str(path))
    return raw, {'path': str(path.resolve()), 'sha256': checksum, 'bytes': len(raw)}


def json_pin(path, checksum):
    raw, pin = read_pin(path, checksum)
    require(len(raw) <= 32 * 1024 * 1024, 'Source report exceeds bound.')
    value = json.loads(raw, parse_constant=lambda text: require(False, 'Nonfinite JSON: ' + text))
    require(type(value) is dict, 'Require a source report object.')
    return value, pin


def write_json(path, value):
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    with path.open('xb') as target:
        target.write(raw)
    return {'path': str(path), 'sha256': digest(raw), 'bytes': len(raw)}


def normalized_delta(old, new, helpers):
    before, after = ast.parse(old), ast.parse(new)
    funcs = lambda tree: {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    a, b = funcs(before), funcs(after)
    require(set(helpers) == {'_snapshot_editor_state', '_prepare_snapshot_editor'}, 'Require exactly the two reviewed editor helpers.')
    require(set(b) == set(a) | set(helpers) and not set(a) & set(helpers), 'Only declared new serializer helpers may be added.')
    expected = ast.dump(ast.parse('_prepare_snapshot_editor(rig, original)').body[0], include_attributes=False)
    require(any(isinstance(value, list) and any(ast.dump(row, include_attributes=False) == expected and index + 1 < len(value) and isinstance(value[index + 1], ast.Expr) and isinstance(value[index + 1].value, ast.Call) and ast.unparse(value[index + 1].value.func) == 'bpy.data.libraries.write' for index, row in enumerate(value) if isinstance(row, ast.Expr)) for node in ast.walk(b['begin_isolated_export']) for _field, value in ast.iter_fields(node)), 'Serializer call must immediately precede libraries.write.')
    class StripCall(ast.NodeTransformer):
        count = 0
        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in helpers:
                require(ast.dump(node, include_attributes=False) == expected, 'Require exact _prepare_snapshot_editor(rig, original) call.')
                self.count += 1
                return None
            return node
    strip = StripCall()
    strip.visit(b['begin_isolated_export'])
    require(strip.count == 1, 'Require exactly one standalone serializer call in begin_isolated_export.')
    after.body = [node for node in after.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in helpers]
    require(ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False), 'Shared evaluation/functions/top-level AST changed outside the exact serializer call.')
    return {'normalized_full_module_ast_equal': True, 'unchanged_existing_function_count': len(a) - 1, 'added_helpers': helpers, 'removed_serializer_call_count': 1}


def build(options):
    require(options.write is True, 'Explicit --write required; preserve all existing projections.')
    _, producer_pin = read_pin(Path(__file__).resolve(), options.producer_sha256)
    new, source_pin = read_pin(SOURCE, options.export_sha256)
    require(source_pin['sha256'] != BEFORE, 'Serializer source is unchanged.')
    reports, report_pins = [], []
    for role in ('owner', 'peer', 'test'):
        report, pin = json_pin(getattr(options, role + '_report'), getattr(options, role + '_report_sha256'))
        require(report.get('passed') is True and report.get('source_only') is True and report.get('native_execution_performed') is False, 'Require actual SOURCE-only PASS ' + role + ' report.')
        sources = report.get('changed_runtime_sources')
        require(type(sources) is dict and set(sources) == {NAME}, 'Report must pin exactly the one serializer source.')
        require(all(sources[NAME].get(key) == source_pin[key] for key in ('path', 'sha256', 'bytes')), 'Report admits different canonical serializer bytes.')
        reports.append(report); report_pins.append(pin)
    delta = reports[0].get('numeric_reuse_delta')
    require(type(delta) is dict and delta == reports[1].get('numeric_reuse_delta') and delta.get('serialization_only') is True, 'Owner and peer must agree exact numeric-reuse scope.')
    require(delta.get('old_projection') == {'path': str(PARENTS[0][0]), 'sha256': PARENTS[0][1]} and delta.get('changed_functions') == {NAME: ['begin_isolated_export']}, 'Different numeric baseline/function scope.')
    helpers = delta.get('added_functions', {}).get(NAME)
    require(type(helpers) is list and helpers and len(helpers) == len(set(helpers)) and all(type(name) is str and name.isidentifier() for name in helpers), 'Require exact new helper names.')
    require(set(delta['added_functions']) == {NAME}, 'Unexpected new helper module.')
    results = reports[2].get('pure_test_results')
    require(type(results) is dict and type(results.get('test_count')) is int and results['test_count'] > 0 and all(type(results.get(key)) is int and results[key] == 0 for key in ('exit_code', 'failures', 'errors')), 'Require actual positive-count pure tests with exit0/failures0/errors0.')
    _, projection_tool_pin = read_pin(REPO / 'tools/release_projection.py', PROJECTION_TOOL_SHA)
    from release_projection import load_source
    parents, snapshots, parent_pins = [], [], []
    for index, (path, checksum) in enumerate(PARENTS):
        parent, pin = json_pin(path, checksum); frozen = load_source('character_designer', path)
        require(frozen.version == '0.77.25' and len(frozen.files) == (167 if index == 0 else 173) and parent['files'][NAME] == BEFORE, 'Wrong immutable parent inventory/version/source.')
        parents.append(parent); snapshots.append(frozen); parent_pins.append(pin)
    proof = normalized_delta(snapshots[0].files[Path(NAME)], new, helpers)
    require(snapshots[0].files[Path(NAME)] == snapshots[1].files[Path(NAME)], 'Parents disagree on serializer bytes.')
    info = parents[1]['source_provenance']
    require(info['python_projection'] == str(PARENTS[0][0]) and info['python_projection_sha256'] == PARENTS[0][1], 'Full parent has wrong Python parent.')
    embedded, embedded_pin = json_pin(info['embedded_overlay_manifest'], info['embedded_overlay_manifest_sha256'])
    require(embedded_pin['sha256'] == 'c3e7742ac5068f2aafb7f9937a59a866bad7f719c92a3bcb7a45154e7d73e85c' and len(embedded['files']) == len(info['canonical_embedded_overlays']) == 8, 'Require unchanged exact eight r6 embedded overlays.')
    embedded_pins = []
    for name, record in info['canonical_embedded_overlays'].items():
        raw, pin = read_pin(record['canonical_source'], record['sha256'])
        require(snapshots[1].files[Path(name)] == raw, 'Embedded source differs from immutable full parent.'); embedded_pins.append(pin)
    require(all(not path.exists() for path in DESTINATIONS) and not RECEIPT.exists(), 'New projections/receipt must not exist.')
    # Rehash every parent leaf and all proof inputs before the first exclusive write.
    for parent, frozen, pin in zip(parents, snapshots, parent_pins):
        read_pin(pin['path'], pin['sha256'])
        for relative, raw in frozen.files.items():
            require(read_pin(Path(parent['source_root']) / relative, parent['files'][relative.as_posix()])[0] == raw, 'Parent leaf changed before write.')
    for pin in [source_pin, producer_pin, projection_tool_pin, embedded_pin, *report_pins, *embedded_pins]:
        read_pin(pin['path'], pin['sha256'])
    fix = {'kind': 'Exact disposable editor RNA snapshot cleanup', 'changed_runtime_sources': {NAME: source_pin}, 'before_sha256': {NAME: BEFORE}, 'owner_source_report': report_pins[0], 'independent_source_review': report_pins[1], 'focused_pure_test_report': report_pins[2], 'numeric_reuse_delta': delta, 'independent_ast_normalization': proof, 'producer': producer_pin, 'native_validation': 'pending'}
    outputs = []
    for index, (parent, frozen, dest) in enumerate(zip(parents, snapshots, DESTINATIONS)):
        dest.mkdir(exist_ok=False)
        package = dest / 'character_designer'; package.mkdir(exist_ok=False)
        for relative, raw in frozen.files.items():
            path = package / relative; path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as target:
                target.write(new if relative.as_posix() == NAME else raw)
        manifest = deepcopy(parent); manifest['source_root'] = str(package); manifest['files'][NAME] = source_pin['sha256']
        provenance = manifest['source_provenance']; provenance.update(native_validation='pending', complete_release=False, previous_projection=parent_pins[index]['path'], previous_projection_sha256=parent_pins[index]['sha256'], composition_snapshot_serializer=fix)
        if index == 0:
            provenance['canonical_python_overlays'][NAME] = {'canonical_source': source_pin['path'], 'sha256': source_pin['sha256']}
        else:
            provenance.update(python_projection=outputs[0]['path'], python_projection_sha256=outputs[0]['sha256'])
        pin = write_json(dest / ('python_projection.json' if index == 0 else 'candidate_projection.json'), manifest)
        verified = load_source('character_designer', pin['path'])
        require(set(verified.files) == set(frozen.files) and {p.as_posix() for p in frozen.files if frozen.files[p] != verified.files[p]} == {NAME}, 'Only one serializer file may change.')
        outputs.append({**pin, 'file_count': len(verified.files), 'changed_product_files': [NAME]})
    receipt = write_json(RECEIPT, {'schema': 'x.validation.character-composition-snapshot-serializer-projections/1', 'source_only': True, 'native_execution_performed': False, 'release_or_deployment_performed': False, 'fix': fix, 'previous_projections': parent_pins, 'python_projection': outputs[0], 'complete_candidate': outputs[1], 'embedded_overlay_manifest': embedded_pin, 'embedded_overlay_count': 8, 'embedded_overlays_unchanged': True})
    print(json.dumps({'projections': outputs, 'receipt': receipt}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('producer-sha256', 'export-sha256', 'owner-report', 'owner-report-sha256', 'peer-report', 'peer-report-sha256', 'test-report', 'test-report-sha256'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--write', action='store_true')
    build(parser.parse_args())


if __name__ == '__main__':
    main()
