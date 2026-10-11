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
TOOLS_RECEIPT_SHA256 = 'f25ac72e961bbb5bc2bdd2c86432d7b807850ae4e03d948004745864bf513b1f'
SESSION_SHA256 = '47cd966e74c50188ddbebd1efe8b915dd9df645184ee7409bf657e756cee6fb3'
SOURCE_REVIEW_SHA256 = '5b75fcad568a7ae630eac8efcab77a545bd3707d32f9467f79fd13f93c36d490'
CONTINUATION_REVIEW_SHA256 = '8b132e81d91ed779a3819287eae35428af37cf641907f5a37daf11c68fc360cd'
INHERITED_CONTINUATION_REVIEW_SHA256 = 'a0bb2c6cec35240ed5da80f4d3a6e2761e057b6522565d37fa378266a607036c'
FROZEN06_SHA256 = 'e0f92f75ae2eeeffe45eee9ba0de9176d230cc75c8ef6c7034dc1461b5641c14'
FROZEN07_SHA256 = '5d84c8ac6f8826e10f92df78a7fcd9a10a6e8360c98f7b729aa8ac4c44a31c48'
FROZEN08_SHA256 = '1b0d106c99302ff66e0f262b695b868604127c507509760ab24de1b8455f0832'
SOURCE_MANIFEST_SHA256 = '4f12871b17cf042618d96e23cfb340939ebcf4764ff10e1843c16f0e4f5c82d7'
SOURCE_ENTRY_SHA256 = '111f933867cfd675b2204d250544518a21b507b106a1ec69020e85b1388bd534'
TOOL_NAMES = {'run_character_composition_first_stage', 'verify_character_composition_native',
    'composition_sync_acceptance', 'composition_timing_acceptance',
    'composition_session_acceptance', 'composition_raise_authoring',
    'run_character_composition_raise_stage', 'run_character_composition_ui_fixture',
    'composition_ui_checkpoints', 'release_projection', 'run_character_composition_first_stage_continuation'}
MAX_JSON_BYTES = 32 * 1024 * 1024
SOURCE_PROOF_FIELDS = {'runtime_projection', 'full_projection', 'embedded_overlay_manifest',
    'parent_root_manifest', 'parent_deploy_compile_receipt', 'parent_prepare_receipt',
    'hold_pose_source_constancy_proof', 'native_source_report', 'tools_source_manifest',
    'snapshot_serializer_projection_receipt', 'range_fix_projection_receipt'}


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
            'Supply the explicitly approved frozen09 receipt and full digest; no old/latest fallback.')
    receipt_path = Path(receipt_path)
    directory = receipt_path.resolve().parent
    require(directory.parent == root.resolve() and directory.name == 'tools_headful_09'
            and receipt_path.name == 'frozen_tools_receipt.json',
            'Use the exact root-owned frozen headful receipt outside mutable UI evidence.')
    receipt_pin = pin(receipt_path, receipt_sha256)
    receipt = read(receipt_pin['path'])
    require(receipt.get('schema') == 'x.validation.character-composition-tools-frozen/1'
            and type(receipt.get('files')) is dict and set(receipt['files']) == TOOL_NAMES,
            'Use the exact eleven-tool immutable headful receipt.')
    pin(receipt['source_manifest']['path'], receipt['source_manifest']['sha256'])
    require(receipt['files']['composition_session_acceptance']['sha256'] == SESSION_SHA256
            and receipt.get('inherited_source_review', {}).get('sha256') == SOURCE_REVIEW_SHA256
            and receipt.get('independent_source_review', {}).get('sha256') == CONTINUATION_REVIEW_SHA256
            and receipt.get('inherited_continuation_source_review', {}).get('sha256') == INHERITED_CONTINUATION_REVIEW_SHA256
            and receipt.get('origin_frozen06_receipt', {}).get('sha256') == FROZEN06_SHA256
            and receipt.get('origin_frozen07_receipt', {}).get('sha256') == FROZEN07_SHA256
            and receipt.get('origin_frozen08_receipt', {}).get('sha256') == FROZEN08_SHA256
            and receipt['source_manifest']['sha256'] == SOURCE_MANIFEST_SHA256
            and receipt['origin_source_entry']['sha256'] == SOURCE_ENTRY_SHA256
            and receipt['delta'] == {'changed_tools': ['run_character_composition_first_stage_continuation'],
                'added_tools': [], 'unchanged_count': 10},
            'Require both inherited EditA witnesses and the reviewed native-default/continuation delta.')
    pin(receipt['inherited_source_review']['path'], SOURCE_REVIEW_SHA256)
    pin(receipt['independent_source_review']['path'], CONTINUATION_REVIEW_SHA256)
    pin(receipt['inherited_continuation_source_review']['path'], INHERITED_CONTINUATION_REVIEW_SHA256)
    pin(receipt['origin_frozen06_receipt']['path'], FROZEN06_SHA256)
    pin(receipt['origin_frozen07_receipt']['path'], FROZEN07_SHA256)
    parent = read(pin(receipt['origin_frozen08_receipt']['path'], FROZEN08_SHA256)['path'])
    require(set(parent['files']) == TOOL_NAMES
            and all(record['sha256'] == parent['files'][name]['sha256'] for name, record in receipt['files'].items()
                    if name != 'run_character_composition_first_stage_continuation')
            and parent['files']['run_character_composition_first_stage_continuation']['sha256'] ==
                'af08e593e350646a0055cb689641631c08e111015a61219b713a45a14d441435'
            and receipt['files']['run_character_composition_first_stage_continuation']['sha256'] ==
                '23cda6830ce26421798df44b69b1c680b63536c393c9f22f16631f52818b411c',
            'Frozen09 must retain exactly ten frozen08 helpers and change only the reviewed continuation.')
    pin(receipt['origin_source_entry']['path'], SOURCE_ENTRY_SHA256)
    for name, record in receipt['files'].items():
        source = pin(record['path'], record['sha256'])
        require(Path(source['path']) == (directory / (name + '.py')).resolve(),
                'Frozen tool receipt escaped its exact sibling directory: ' + name)
    runner = load(receipt['files']['run_character_composition_ui_fixture']['path'], '_first_ui_frozen_runner')
    native = load(receipt['files']['verify_character_composition_native']['path'], '_first_ui_frozen_native')
    checkpoints = load(receipt['files']['composition_ui_checkpoints']['path'], '_first_ui_frozen_checkpoints')
    return receipt_pin, receipt['files'], runner, native, checkpoints


def require_loop_operator_proof(author):
    """Require native selection/context and a finite, positive real FK operation."""
    diagnostic = author['operator_diagnostics']
    require(author['pendingNativeOperator'] is False and author['checkpoint_stage'] == 'native_rotate_verified'
            and author['checkpoint_phase'] == 'author_actual_HoldLoop_Custom'
            and diagnostic['pendingNativeOperator'] is False
            and diagnostic['checkpoint_stage'] == 'native_rotate_verified' and diagnostic['actualundo_requested'] is False,
            'The real background native transform must complete its persisted checkpoint without requesting HUD undo.')
    control, field = diagnostic['control'], diagnostic['field']
    require(control == author['authoring']['control'] and diagnostic['native_sources']
            and diagnostic['initial_rotation']['valid'] is True
            and diagnostic['initial_rotation']['finite'] is True, 'Actual chosen native FK input is unproved.')
    frames = diagnostic['frames']
    require([row['phase'] for row in frames] == ['initial_input', 0., .5, 1.], 'Every actual mapped frame needs a native readback.')
    for row in frames:
        require(row['visibility']['visible'] is True and row['visibility']['pose_hidden'] is False
                and row['object_mode'] == row['context_mode'] == 'POSE'
                and row['area_type'] == row['space_type'] == 'VIEW_3D' and row['region_type'] == 'WINDOW'
                and row['active_object'] == row['pose_object'] and row['active_object']
                and row['tweak_mode'] is True and row['action'] and type(row['action_slot']) is int and row['action_slot'] >= 0
                and row['action'] == frames[0]['action'] and row['action_slot'] == frames[0]['action_slot']
                and row['selected_after'] == row['selected_pose_bones'] == [control] and row['active_bone'] == control
                and all(type(row[key]) in (int, float) and math.isfinite(row[key]) for key in ('B_frame', 'C_frame')),
                'Native mapped-frame selection/Pose/View3D/Tweak/action/slot proof failed.')
        require(row['actualundo_requested'] is False, 'Background native operator undo must remain false on every mapped frame.')
    operation = frames[2]['operator']
    require(operation['poll'] is True and operation['result'] == ['FINISHED'] and 'exception' not in operation
            and operation['minimum_rotation_radians'] == 1e-7
            and operation['actualundo_requested'] is False and operation['pendingNativeOperator'] is False
            and operation['checkpoint_stage'] == 'native_rotate_verified', 'Actual middle native operator must finish without fallback.')
    for state in (operation['before'], operation['after']):
        require(state['field'] == field and state['finite'] is True and state['valid'] is True
                and len(state['values']) == (3 if field == 'rotation_euler' else 4)
                and len(state['basis']) == 16 and len(state['quaternion']) == 4
                and all(type(value) in (int, float) and math.isfinite(value)
                        for key in ('values', 'basis', 'quaternion') for value in state[key]),
                'Native before/after FK rotation values are invalid.')
    left, right = operation['before']['quaternion'], operation['after']['quaternion']
    denominator = math.sqrt(sum(v*v for v in left) * sum(v*v for v in right))
    require(math.isfinite(denominator) and denominator > 0., 'Invalid native rotation normalizer.')
    cosine = abs(sum(a*b for a,b in zip(left,right))) / denominator
    require(math.isfinite(cosine), 'Invalid native FK comparison.')
    delta = 2. * math.acos(min(1., max(0., cosine)))
    require(type(operation['rotation_delta_radians']) in (int,float) and math.isfinite(operation['rotation_delta_radians'])
            and math.isfinite(delta) and delta > 1e-7 and abs(delta-operation['rotation_delta_radians']) <= 1e-12,
            'Actual finished operator has no finite positive native rotation witness.')
    return {'control': control, 'field': field, 'result': ['FINISHED'], 'rotation_delta_radians': delta,
            'mapped_frame_selection_readbacks': 4, 'native_full_stack_action_slot_retained': True}


def require_range_fix_reuse(entry, admission, numeric, record_pin):
    """Share the pinned pure SOURCE range predicate; never run native work."""
    row = entry['range_fix_projection_receipt']
    require(row == admission['range_fix_projection_receipt']
            and row['sha256'] == '001311420fbe4f255ed18b4fe5fb69680f2f7048d0cbe9eaee8068f1f68e34bc',
            'Bind the separate native-frame admission proof; serializer facts remain history.')
    record_pin(row['path'], row['sha256'])
    source = record_pin(entry['continuation']['entry']['path'], entry['continuation']['entry']['sha256'])
    require(source['sha256'] == '23cda6830ce26421798df44b69b1c680b63536c393c9f22f16631f52818b411c',
            'Use only the exact approved pure continuation range checker.')
    module = load(source['path'], '_first_ui_pure_range_admission')
    precision = module.range_projection(admission)
    require(precision == read(row['path'])
            and precision['previous_projections'][0]['sha256'] == '5c1f5da5b46bf4f888f8ab226a86295775dae9020bd2af8d07f78e9336c1ae69'
            and precision['previous_projections'][1]['sha256'] == '4dd0fb1d45ba03b7723a899a7e3614a023120c8dd8e813e9678932e910194c94'
            and {key: precision['python_projection'][key] for key in ('path','sha256')} == entry['runtime_projection']
            and {key: precision['complete_candidate'][key] for key in ('path','sha256')} == entry['full_projection']
            and entry['runtime_projection']['sha256'] == 'c1dc22a65fb20a5372972337282cd78be195cb0cfec0382075390506fd43e53c'
            and entry['full_projection']['sha256'] == '172ee6cf99167d3e23a620dd56bd0219a51dfa0e0f39686c9be1646399cc7cf1',
            'Require exact historical serializer parents and the current guard-only projection pair.')
    def record_graph(value):
        if type(value) is dict:
            if {'path','sha256'} <= set(value):
                record_pin(value['path'], value['sha256'])
            for child in value.values(): record_graph(child)
        elif type(value) is list:
            for child in value: record_graph(child)
    record_graph(precision)
    if numeric is not None:
        require(numeric['numeric_reuse']['range_fix_projection_receipt'] == row
                and numeric['numeric_reuse']['range_fix_delta'] == {
                    'changed_functions': precision['fix']['changed_functions'],
                    'previous_projection': precision['previous_projections'][0],
                    'projection': precision['python_projection'], 'timing_admission_only': True},
                'Numeric reuse must report the range guard separately from historical serialization.')
    return precision


def require_physical_tail_reuse(entry, first, admission, audit, record_pin):
    """Authenticate inherited publications without relabeling the failed n12 run."""
    root = Path(entry['config']['path']).resolve().parent.parent
    def checked(row):
        return Path(record_pin(row['path'], row['sha256'])['path'])
    tail = admission['physical_tail']
    expected = {
        'first_stage_report': ('n12/composition_first_stage_report.json', '916860cd83d4c2eafbc346017659855d96ba42f1f3bcaeea2fb182e365c4467d'),
        'native_report': ('n12/composition_native_report.json', '32d5178e73f2900d21add8518ec6fd45fc4df40c41586f11a4abb00257d06eb9'),
        'physical_report': ('n12/physical/composition_sync_report.json', 'd1624f1b737b90c162a0fb1d0436102c59b20d20acf46b675fe9c675f179d5af'),
        'loop_failure': ('n12/loop_author/composition_HoldLoop_authoring_report.json', '67d00b7fe4529e60eeacd7aff58f40d7a268414b92674ff7b58b80e7e9f50a43'),
        'failure_readback': ('n12_natural_failure_readback_916860cd.json', '041253e90a8baae9ed715c28b4fde0e002aeea3e57ff595b41911e08011f5ff7'),
        'source_entry': ('n12/source_entry_receipt.json', '845394a52e7ba286c20fe100f1bbd69a559ae7771678b3589b906bb3acec31ec')}
    require(type(tail) is dict and set(tail) == {'schema', *expected, 'owner_review', 'peer_review', 'latest_failure_readback', 'latest_exit', 'latest_source_entry'}
            and tail['schema'] == 'x.validation.character-composition-physical-tail-reuse/2',
            'Use exact six-history/owner/peer Loop-tail admission.')
    data = {}
    for key, (relative, digest) in expected.items():
        require(checked(tail[key]) == root / relative and tail[key]['sha256'] == digest,
                'The failed n12 evidence pin differs: ' + key)
        data[key] = read(checked(tail[key]))
    old, numeric, physical, failed, failure, origin = [data[key] for key in expected]
    require(old['completed'] is False and old['continuation'] is True and old['numeric'] == numeric
            and old['two_a_physical'] == physical and 'loop_physical' not in old and 'qa_seed' not in old
            and numeric['completed'] is False and numeric['numeric_completed'] is True
            and failed['complete'] is False and failed['failure_phase'] == 'author_actual_HoldLoop_Custom'
            and old['error'] == 'The actual native HoldLoop middle FK Pose rotation did not finish.'
            and failure['passed'] is True and failure['source_only'] is True and failure['attempt'] == 'n12'
            and failure['native_run_completed'] is False and failure['full_acceptance_passed'] is False
            and failure['actual_two_pose_exports_complete'] is True and failure['qa_seed_created'] is False
            and failure['third_source_published'] is False and failure['loop_authoring_complete'] is False
            and failure['natural_exit_record']['naturalExitCode'] == 1
            and failure['natural_exit_record']['forcedTermination'] is False
            and failure['natural_exit_record']['allQaBlenderWorkersGoneVerified'] is True,
            'Keep actual n12 Loop failure/partial native status and natural exit unchanged.')
    for row in failure['files']: checked(row)
    require(first['physical_tail'] == tail and first['two_a_physical'] == physical
            and first['physical_source_publications_inherited'] == 2
            and first['physical_source_publications_executed'] == 1
            and first['first_two_source_reexport_performed'] is False
            and first['first_two_link_revisions_unchanged'] is True,
            'Exactly two inherited SOURCEs and one actually executed Loop are required.')
    reviews = [read(checked(tail[key])) for key in ('owner_review', 'peer_review')]
    require(all(review['passed'] is True and review['source_only'] is True
                and review['native_execution_performed'] is False for review in reviews)
            and reviews[0]['reviewed_source_changes'] == reviews[1]['reviewed_source_changes'],
            'The exact three tail source deltas need independent SOURCE review.')
    before = {'run_character_composition_first_stage_continuation.py': '0da0072321607a2e5e31974a8bc4889d04fcb670430b8cebf0dd7443e231f2f9',
              'composition_timing_acceptance.py': '58325c8b4256585a12370aa10a059eabb1d80c74fe3d2eb4b2fbb97fecf0cb06',
              'build_character_composition_continuation_fixture.py': '239a2b187106dc5592da74fc2bae12c5e37df87356ffc233cd50f4646ecef339'}
    changes = reviews[0]['reviewed_source_changes']
    expected_paths = {str(Path(r'D:\MyRepository\Blender-addons-by-Randy\tools') / name) for name in before}
    require(set(changes) == expected_paths, 'Tail review contains an unrelated source replacement.')
    for path, delta in changes.items():
        require(set(delta) == {'before_sha256', 'after_sha256'} and delta['before_sha256'] == before[Path(path).name]
                and origin['source_inputs'][path] == delta['before_sha256'], 'Reviewed tail baseline differs from n12.')
        checked({'path': path, 'sha256': delta['after_sha256']})
    tools = Path(r'D:\MyRepository\Blender-addons-by-Randy\tools')
    require(entry['continuation']['entry']['sha256'] == changes[str(tools / 'run_character_composition_first_stage_continuation.py')]['after_sha256']
            and entry['tools']['composition_timing_acceptance']['sha256'] == changes[str(tools / 'composition_timing_acceptance.py')]['after_sha256']
            and entry['source_builder']['sha256'] == changes[str(tools / 'build_character_composition_continuation_fixture.py')]['after_sha256']
            and checked(entry['origin_source_entry']) == checked(tail['source_entry'])
            and entry['origin_source_entry']['sha256'] == tail['source_entry']['sha256'],
            'Frozen continuation/timing/producer or original n12 source-entry differs from reviewed admission.')
    published = []
    for row in physical['items']:
        for path_key, sha_key in (('manifestPath', 'manifest_after_sha256'), ('fbx_file', 'fbx_sha256'), ('metadata_file', 'metadata_sha256')):
            record = {'path': row[path_key], 'sha256': row[sha_key]}; checked(record); published.append(record)
    require(len(physical['items']) == 2 and len(published) == 6
            and {row['path']: row['sha256'] for row in failure['authorized_source_manifest_changes']}
                == {row['manifestPath']: row['manifest_after_sha256'] for row in physical['items']},
            'Actual six inherited publication bytes differ from the authorized two Links.')
    latest_expected = {
        'latest_failure_readback': ('n14_natural_failure_readback_43b9cf86.json', '829c2758f1e7b33e52ed46571891a55d2dc31313a9816fc9cb951f607e459488'),
        'latest_exit': ('n14/process_exit.json', '04a8fb7185d7080169bbb8708ff6f5838269d319a6f30a9492a38e29baf14bc2'),
        'latest_source_entry': ('n14/source_entry_receipt.json', 'f2b38a3aca7639618396c00db45c8d9a6e25020bc3d71321a55feea1a51ad1cc')}
    for key, (relative, digest) in latest_expected.items():
        require(checked(tail[key]) == root / relative and tail[key]['sha256'] == digest,
                'The consumed n14 actual failure pin differs: ' + key)
    latest, exited, latest_entry = [read(checked(tail[key])) for key in latest_expected]
    require(latest['schema'] == 'x.validation.character-composition-native-failure-protection/1'
            and latest['passed'] is True and latest['source_only'] is True
            and latest['attempt'] == 'n14' and latest['completed'] is False
            and latest['source_input_count'] == 964 and latest['source_inputs_all_unchanged'] is True
            and latest['full_outer_native_recovery_completed'] is False
            and latest['outer_post_scope_original_action_and_raw_asset_comparisons_not_reached'] is True
            and latest['loop_authoring_native_keys_and_commit_proven'] is True
            and latest['native_rotate_finished_count'] == 1 and latest['native_keyframe_insert_count'] == 3
            and latest['native_selection_readback_count'] == 4
            and latest['loop_authoring_complete'] is False and latest['loop_export_performed'] is False
            and latest['owned_qa_seed_created'] is False and latest['u1_generated'] is False
            and latest['actual_failure_phase'] == 'measure_genuine_foreground_B_for_physical_Sync'
            and latest['actual_error'] == 'Moving B sample bounds exceed its prepared duration or authored selected-slot FK range.'
            and latest['native_process_natural_exit_code'] == 1
            and latest['automatic_retry_or_forced_termination_performed'] is False
            and latest['actual_process_exit_values'] == exited and exited['attempt'] == 'n14'
            and exited['naturalExitCode'] == 1 and exited['forcedTermination'] is False
            and exited['allQaBlenderWorkersGoneVerified'] is True
            and checked(latest['actual_process_exit']) == checked(tail['latest_exit'])
            and checked(latest['source_entry']) == checked(tail['latest_source_entry'])
            and latest['inherited_two_source_publications_unchanged'] is True
            and latest['inherited_two_source_revisions'] == [1, 1]
            and latest['fresh_isolated_variants_inherited'] == 2 and latest['fresh_isolated_variants_executed'] == 0
            and latest['current_UI_imports_executed'] == 0,
            'Preserve the actual partial native n14 failure; never infer successful Loop/outer acceptance.')
    for path in latest['actual_absent_success_paths']:
        if Path(path).resolve().is_relative_to(root / 'n14'):
            require(not Path(path).exists(), 'Failed n14 owned success artifact appeared.')
    checks = {row['path']: row for row in latest['source_input_checks']}
    require(len(checks) == len(latest['source_input_checks']) == len(latest_entry['source_inputs']) == 964
            and set(checks) == set(latest_entry['source_inputs'])
            and all(checks[path]['expected_sha256'] == checks[path]['actual_sha256'] == value
                    for path, value in latest_entry['source_inputs'].items()), 'Latest exact964 source baseline is incomplete.')
    latest_before = {'run_character_composition_first_stage_continuation.py': 'af08e593e350646a0055cb689641631c08e111015a61219b713a45a14d441435',
                    'composition_timing_acceptance.py': 'a89ffd1a3acf4937ef033bf4dfad1c77bc34ec883bc032f7dc0cf44e1bb99874',
                    'build_character_composition_continuation_fixture.py': 'ec25dc21856d27d7b785fa58c2b78e8e8009ad96d68381c17b325a14d206f112'}
    precision = require_range_fix_reuse(entry, admission, None, record_pin)
    superseded = Path(precision['fix']['superseded_canonical_source_path']).resolve()
    loop_publication = first['loop_physical']
    for path, value in latest_entry['source_inputs'].items():
        if path in changes:
            require(value == latest_before[Path(path).name], 'Latest reviewed tool baseline changed.')
            expected_value = changes[path]['after_sha256']
        elif Path(path).resolve() == superseded:
            require(value == '2dd2173ec604918328b25945f22916d96ce8039a322c7ed034ee65f686b69984'
                    and precision['fix']['superseded_source']['sha256'] == value,
                    'The replaced historical exporter must have immutable native runtime11 proof.')
            checked(precision['fix']['superseded_source'])
            expected_value = '562565879a9c4c05356f82c3b0ec45bd567d1311ef58cbde8a20607452079520'
        elif Path(path).resolve() == Path(loop_publication['manifestPath']).resolve():
            require(value == loop_publication['manifestSha256'], 'The third SOURCE must originate in the admitted Loop Link.')
            expected_value = loop_publication['manifest_after_sha256']
        else:
            expected_value = value
        checked({'path': path, 'sha256': expected_value})
    retained = {row['path']: row['sha256'] for row in latest['inherited_two_source_publications']}
    require(len(latest['inherited_two_source_publications']) == len(retained) == 6
            and retained == {row['path']: row['sha256'] for row in published},
            'The exact six inherited SOURCE publication files must remain unchanged.')
    require(numeric['numeric_completed'] is True and numeric['completed'] is False
            and numeric['runtime']['files'] == 167 and numeric['numeric_reuse']['cases_reexecuted'] is False
            and numeric['numeric_reuse']['fresh_isolated_variants_verified'] == 2,
            'Inherit only actual persisted n12 two-variant native numeric proof.')
    summary = {'admission': tail, 'persisted_numeric': numeric, 'failed_attempt_completed': False, 'failed_loop_completed': False,
            'inherited_source_count': 2, 'executed_source_count': 1, 'inherited_publication_files': published}
    require(audit['numeric_reuse']['physical_tail'] == summary,
            'Completion changed the exact inherited physical-tail history/publication proof.')
    return summary


def require_loop_receipt(author, physical):
    """Bind the third actual SOURCE publication to its independent raw B proof."""
    require_loop_operator_proof(author)
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
        and audit.get('full_native_count') == 217 and audit.get('current_UI_associations') == 2
        and audit.get('historical_original_recovery') == 'historical_native_state_and_rows_restored'
        and audit.get('artist_input_unchanged') is True
        and len(audit.get('publications', [])) == 3 and all(row['kind'] == 'SOURCE' for row in audit['publications']),
        'Require the actual complete three-SOURCE closure, natural exit0 and all-worker absence proof.')
    require(all(type(closure.get(key)) is type(value) and type(audit.get(key)) is type(value)
        and closure[key] == audit[key] == value for key, value in (
        ('numeric_cases_reused', 12), ('numeric_cases_reexecuted', 0),
        ('fresh_isolated_variant_count', 0), ('fresh_isolated_variants_inherited', 2), ('current_UI_associations', 2),
        ('physical_source_publications_inherited', 2), ('physical_source_publications_executed', 1),
        ('first_two_source_reexport_performed', False), ('first_two_link_revisions_unchanged', True)))
        and type(audit.get('loop_operator_proof')) is dict
        and closure.get('loop_operator_proof') == audit['loop_operator_proof']
        and audit['loop_operator_proof'].get('result') == ['FINISHED'],
        'Completion must distinguish inherited two SOURCEs/one real Loop and actual native operator proof.')
    require(audit.get('config') == config_pin and audit.get('source_entry') == source_pin
        and closure.get('immutable_source_proofs') == audit.get('immutable_source_proofs')
        and set(audit['immutable_source_proofs']) == SOURCE_PROOF_FIELDS
        and closure.get('range_fix_projection_receipt') == audit.get('range_fix_projection_receipt')
            == audit['immutable_source_proofs']['range_fix_projection_receipt'],
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


def require_numeric_reuse(stage, numeric, entry, audit, record_pin):
    """Keep the twelve old cases distinct from two fresh isolated proofs."""
    continuation, reuse = entry['continuation'], numeric['numeric_reuse']
    require(stage.get('continuation') is True and audit.get('numeric_cases_reused') == 12
            and audit.get('numeric_cases_reexecuted') == 0 and audit.get('fresh_isolated_variant_count') == 0
            and audit.get('fresh_isolated_variants_inherited') == 2 and audit.get('current_UI_associations') == 2,
            'Require explicit twelve-case reuse, zero reexecution and two fresh isolated variants.')
    launched = record_pin(continuation['entry']['path'], continuation['entry']['sha256'])
    require(Path(launched['path']).name == 'run_character_composition_first_stage_continuation.py'
            and launched['sha256'] == '23cda6830ce26421798df44b69b1c680b63536c393c9f22f16631f52818b411c',
            'The actual completed continuation entry differs from its approved source.')
    admission_pin = record_pin(continuation['admission']['path'], continuation['admission']['sha256'])
    require(record_pin(stage['reuse_admission']['path'], stage['reuse_admission']['sha256']) == admission_pin,
            'The completed stage used another numeric-reuse admission.')
    admission = read(admission_pin['path'])
    require(set(admission) == {'schema', 'numeric_report', 'failure_readback', 'owner_review', 'peer_review', 'projection_receipt', 'physical_tail', 'range_fix_projection_receipt'}
            and admission['schema'] == 'x.validation.character-composition-numeric-reuse/1'
            and admission['numeric_report']['sha256'] == 'c5d1170c43848b5d6ccb607953c7dad0cecbeb52291a81b2d490ead3020dfb6c'
            and admission['failure_readback']['sha256'] == '2291e1e7360b8431eafb0d2fd86b12eafe486d96a2760b58051867468a0a6189'
            and admission['projection_receipt']['sha256'] == 'cbf7528f2c014389ba3b4d29adc359e7dd89ec25e460b5cf51674f34ef8145cb'
            and admission['projection_receipt'] == entry['snapshot_serializer_projection_receipt'],
            'Only the actual partial-numeric failure and serializer-only projection authorize this reuse.')
    for row in admission.values():
        if type(row) is dict and 'path' in row:
            record_pin(row['path'], row['sha256'])
    precision = require_range_fix_reuse(entry, admission, numeric, record_pin)
    tail = require_physical_tail_reuse(entry, stage, admission, audit, record_pin)
    serializer = read(admission['projection_receipt']['path'])
    require(serializer['schema'] == 'x.validation.character-composition-snapshot-serializer-projections/1'
            and serializer['source_only'] is True and serializer['native_execution_performed'] is False
            and serializer['embedded_overlays_unchanged'] is True and serializer['embedded_overlay_count'] == 8
            and admission['owner_review'] == serializer['fix']['owner_source_report']
            and admission['peer_review'] == serializer['fix']['independent_source_review'],
            'Require the exact independent serializer owner/peer and unchanged embedded source proof.')
    for field in ('owner_review', 'peer_review'):
        review = read(admission[field]['path'])
        require(review['source_only'] is True and review['native_execution_performed'] is False
                and review['passed'] is True and review['numeric_reuse_delta'] == serializer['fix']['numeric_reuse_delta'],
                'Numeric reuse is not bound to the independently reviewed serializer-only delta.')
    old, failure = read(admission['numeric_report']['path']), read(admission['failure_readback']['path'])
    require(old['completed'] is False and old['numeric_completed'] is True
            and failure['native_run_completed'] is False and failure['full_acceptance_passed'] is False
            and failure['evidence_readback_passed'] is True and len(failure['source_rehash']) == 28,
            'Do not relabel the original failed n11 run as a completed native acceptance.')
    require(reuse['source'] == admission['numeric_report'] and reuse['old_report_completed'] is False
            and reuse['cases_reexecuted'] is False and reuse['fresh_isolated_variants_inherited'] == 2
            and reuse['fresh_isolated_variants_executed'] == 0
            and reuse['persisted_fresh_native_source'] == admission['physical_tail']['native_report']
            and numeric['fresh_isolated_variants_inherited'] == stage['fresh_isolated_variants_inherited'] == 2
            and numeric['fresh_isolated_variants_executed'] == stage['fresh_isolated_variants_executed'] == 0
            and numeric['fresh_packet_proofs_source'] == admission['physical_tail']['native_report']
            and reuse['serializer_delta'] == serializer['fix']['numeric_reuse_delta']
            and len(numeric['cases']) == len(old['cases']) == 12 and numeric['cases'] == old['cases']
            and numeric['effective_values'] == old['effective_values'],
            'The twelve cases/raw B values must be exact immutable reuse; current isolated verification remains explicit.')
    proof = audit['numeric_reuse']
    require(proof['source'] == admission['numeric_report'] and proof['failure_readback'] == admission['failure_readback']
            and proof['admission'] == continuation['admission'] and proof['numeric_cases_reused'] == 12
            and proof['numeric_cases_reexecuted'] == 0 and proof['fresh_isolated_variant_count'] == 0
            and proof['fresh_isolated_variants_inherited'] == 2 and proof['current_UI_associations'] == 2
            and proof['range_fix_projection_receipt'] == admission['range_fix_projection_receipt']
            and proof['range_fix_delta'] == reuse['range_fix_delta'],
            'The completion audit changed the actual reuse evidence or counts.')
    persisted = tail['persisted_numeric']
    require(numeric['fresh_packet_proofs'] == persisted['fresh_packet_proofs']
            and numeric['fresh_associations'] == persisted['fresh_associations']
            and numeric['current_ui_native_associations_verified'] is True
            and len(numeric['currentImportedForUI']) == 2,
            'Persisted two-variant proofs and actual current UI association evidence must be distinguished.')
    ui = numeric['currentImportedForUI']
    require(len({row['item_id'] for row in ui}) == 2 and all(uuid.UUID(row['item_id']).int for row in ui),
            'Current UI rows must have two actual unique native IDs.')
    for index, row in enumerate(ui):
        current = row['packet_rest_proof']; prior = persisted['fresh_packet_proofs'][index]
        require(set(row) == {'item_id','packet_rest_proof','association','b_fingerprint','numeric_resampled','isolated_sync_executed','source_export_executed'}
                and row['numeric_resampled'] is False and row['isolated_sync_executed'] is False and row['source_export_executed'] is False
                and {key: value for key,value in current.items() if key != 'mapping_error_metres'} ==
                    {key: value for key,value in prior.items() if key != 'mapping_error_metres'}
                and current['native_count'] == 217 and type(current['mapping_error_metres']) in (int,float)
                and math.isfinite(current['mapping_error_metres']) and 0 <= current['mapping_error_metres'] <= .0002
                and row['association'] == persisted['fresh_associations'][index]
                and row['b_fingerprint'] == next(case['b_fingerprint'] for case in old['cases'] if case['reference'] == 'a' + str(index+1)),
                'Actual current UI packet/Rest/native217/association/B fingerprint differs or claims reexecution.')
    precondition = numeric['actual_loop_bind_preconditions']
    require(precondition['native_count'] == 217 and precondition['full_bones_metadata_equal'] is True
            and precondition['fk_matcher_fields_equal'] == ['mask', 'allowedBonePaths', 'humanRoles', 'editableControlIds', 'sourceRestSha256', 'weaponRigBonePath', 'gripTargets']
            and identifier(precondition['work_item_id']) == identifier(stage['loop_authoring']['numeric_proof']['spec']['editWorkItemId'])
            and type(precondition['mapping_error_metres']) in (int,float) and math.isfinite(precondition['mapping_error_metres'])
            and 0 <= precondition['mapping_error_metres'] <= .0002
            and precondition['source_rest_sha256'] == persisted['fresh_packet_proofs'][0]['original_rest_sha256']
            and precondition['motion_rest_sha256'] == persisted['fresh_packet_proofs'][0]['motion_rest_sha256'],
            'Current actual Loop native217 bind/Rest/common FK domain admission is missing.')
    packet_keys = ('model_sha256', 'packet_sha256', 'source_rest_sha256', 'original_rest_sha256', 'motion_rest_sha256', 'native_count')
    require(len(numeric['fresh_packet_proofs']) == len(numeric['fresh_associations']) == 2, 'Two actual fresh native proofs are mandatory.')
    for index in range(2):
        current, prior = numeric['fresh_packet_proofs'][index], old['fresh_packet_proofs'][index]
        require(identifier(current['work_item_id']) == identifier(prior['work_item_id'])
                and all(current[key] == prior[key] for key in packet_keys) and current['native_count'] == 217
                and type(current['mapping_error_metres']) in (int, float) and math.isfinite(current['mapping_error_metres'])
                and 0 <= current['mapping_error_metres'] <= .0002, 'Fresh stable source/Rest/full217 proof differs.')
        current, prior = numeric['fresh_associations'][index], old['fresh_associations'][index]
        require(all(current[key] == prior[key] for key in ('model_sha256', 'packet_sha256', 'link_id', 'action_slot'))
                and Path(current['manifest_path']).resolve() == Path(prior['manifest_path']).resolve(),
                'Fresh model/manifest/packet/Link/selected native slot association differs.')


def require_ui_rotation_control(numeric, author, selected_spec):
    """Use the actually operated input only when the selected Hold's scope proves it."""
    diagnostic, loop_spec = author['operator_diagnostics'], author['numeric_proof']['spec']
    scope = numeric['qa_fk_post_adoption']['scope_proof']
    require(loop_spec['sourceRestSha256'] == selected_spec['sourceRestSha256'] == scope['sourceRestSha256']
            and loop_spec['allowedBonePaths'] == selected_spec['allowedBonePaths']
            and scope['sourceRest'] == numeric['fresh_packet_proofs'][0]['original_rest_sha256']
            and scope['motionRest'] == numeric['fresh_packet_proofs'][0]['motion_rest_sha256'],
            'The operated Loop and selected HoldPose must have exact Rest/path scope provenance.')
    pairs = scope['effectiveInputs']
    require(type(pairs) is list and pairs
            and all(type(row) is list and len(row) == 2 and all(type(value) is str and value for value in row)
                    for row in pairs) and len({row[0] for row in pairs}) == len(pairs),
            'The actual post-adoption FK input map is missing or ambiguous.')
    inputs, control = dict(pairs), diagnostic['control']
    paths = scope['nativePaths']
    require(type(control) is str and control and control in inputs.values()
            and sorted(row[1] for row in paths) == sorted(selected_spec['allowedBonePaths'])
            and diagnostic['native_sources'] == sorted(name for name, value in inputs.items() if value == control),
            'Actual Loop input is not the exact native FK candidate in the selected HoldPose scope.')
    return control, {'control': control, 'native_source_names': diagnostic['native_sources'],
        'sourceRestSha256': selected_spec['sourceRestSha256'], 'original_rest_sha256': scope['sourceRest'],
        'motion_rest_sha256': scope['motionRest'], 'selected_work_item_id': selected_spec['editWorkItemId'],
        'actual_Loop_operator_proof_used': True, 'runtime_selected_Hold_scope_revalidation_required': True,
        'Raise_control_binding_deferred': True}


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
    frozen_receipt = read(tools_pin['path'])
    for field in ('source_manifest','independent_source_review','inherited_source_review',
                  'inherited_continuation_source_review','origin_frozen06_receipt','origin_frozen07_receipt','origin_frozen08_receipt',
                  'continuation_owner_source_report','operator_owner_source_report'):
        row = frozen_receipt[field]
        record_pin(row['path'],row['sha256'])
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
        and entry.get('config') == stage_config_pin
        and record_pin(frozen_receipt['origin_source_entry']['path'], frozen_receipt['origin_source_entry']['sha256']) == source_pin
        and record_pin(frozen_receipt['source_manifest']['path'], frozen_receipt['source_manifest']['sha256'])
            == record_pin(entry['tools_source_manifest']['path'], entry['tools_source_manifest']['sha256']),
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
    require_numeric_reuse(stage, numeric, entry, audit, record_pin)
    seed = numeric.get('qa_seed')
    require(type(seed) is dict and set(seed) == runner.SEED_FIELDS
            and seed['itemIds'] == [row['item_id'] for row in numeric['currentImportedForUI']],
            'No authenticated native QA seed receipt matching the two current UI rows is available.')
    physical_pin = record_pin(seed['physicalReport'], seed['physicalReportSha256'])
    require(physical_pin == audit['reports']['two_a_physical'], 'Seed and completion closure pin different two-A physical bytes.')
    physical = read(physical_pin['path'])
    require_completed_stage(stage, numeric, physical)
    require(require_loop_operator_proof(stage['loop_authoring']) == audit['loop_operator_proof'],
            'Completion operator summary differs from independently checked actual Loop diagnostics.')
    require(record_pin(stage['config'],stage['config_sha256']) == stage_config_pin,
        'The completed report no longer binds the explicitly supplied first-stage config.')
    stage_config = read(stage_config_pin['path'])
    require(stage_config.get('schema') == 'x.validation.character-composition-first-stage/1'
        and stage_config['driverSha256'] == files['run_character_composition_first_stage']['sha256']
        and set(stage_config['tools']) == {'verify_character_composition_native', 'composition_sync_acceptance',
            'composition_timing_acceptance', 'composition_session_acceptance', 'release_projection'}
        and set(entry['tools']) == set(stage_config['tools']) | {'run_character_composition_first_stage',
            'run_character_composition_first_stage_continuation'},
        'The actual completed first-stage driver differs from the approved frozen source.')
    for name,row in stage_config['tools'].items():
        require(name in files and record_pin(row['path'],row['sha256'])['sha256'] == files[name]['sha256'],
            'The actual completed first stage used another helper: ' + name)
    for name,row in entry['tools'].items():
        require(name in files and record_pin(row['path'],row['sha256'])['sha256'] == files[name]['sha256'],
            'Source-entry helper differs from approved frozen09: ' + name)
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
    selected_spec = next(spec for spec in specs if identifier(spec['editWorkItemId']) == identifier(selected))
    rotation_control, rotation_control_proof = require_ui_rotation_control(numeric, stage['loop_authoring'], selected_spec)
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
        'rotationControl': rotation_control,
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
        'numeric_reuse':audit['numeric_reuse'], 'numeric_cases_reused':12,
        'numeric_cases_reexecuted':0, 'fresh_isolated_variant_count':0,
        'fresh_isolated_variants_inherited':2, 'current_UI_associations':2,
        'physical_source_publications_inherited':2, 'physical_source_publications_executed':1,
        'first_two_source_reexport_performed':False, 'first_two_link_revisions_unchanged':True,
        'loop_operator_proof':audit['loop_operator_proof'], 'rotation_control_binding':rotation_control_proof,
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
