"""Explicit controls-only input work on an authenticated existing Main Rig.

Legacy Body Actions are read-only preview references.  The two new carriers
contain only the twenty declared Target/Hint scalar channels.  No Body import,
model replacement, worklist reassignment, FBX export or solved-bone capture.
"""
import hashlib
import json
import math
from pathlib import Path
import struct
import uuid

import bpy
from . import animation_composition as composition
from . import animation_rigging_contract as contract
from . import animation_worklist_target as target
from . import bone_display, unity_animation as ua, unity_animation_rigging as native

PAIR_KEY = 'character_designer_original_inputs_pair_v1'
PAIR_META = 'character_designer_original_inputs_owner_v1'
PAIR_REFS = 'character_designer_original_inputs_refs_v1'
PUBLICATION_SCHEMA = 'cdesigner.controls-only-publication/1'


def _require(value, message):
    if not value: raise ValueError(message)


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def _pin(path, digest):
    p = Path(path).resolve()
    _require(isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
             and p.is_file() and _sha(p) == digest, 'An authenticated Original input file changed: ' + str(p))
    return {'path': str(p), 'sha256': digest, 'bytes': p.stat().st_size}


def _read(row):
    _pin(row['path'], row['sha256'])
    return json.loads(Path(row['path']).read_text(encoding='utf-8-sig'))


def _same(a, b):
    return a is not None and b is not None and type(a) is type(b) and a.as_pointer() == b.as_pointer()


def _f32(value):
    _require(type(value) in (int, float) and math.isfinite(value), 'Rest lineage contains a nonfinite value.')
    return struct.pack('<f', value)


def _lineage(legacy, packet):
    for key in ('schema', 'units', 'coordinate', 'matrixLayout', 'targetGuid', 'targetAssetPath', 'projectId'):
        _require(legacy[key] == packet[key], 'Original and prepared character coordinates/identity differ: ' + key)
    _require(len(legacy['bones']) == len(packet['bones']), 'The declared skeleton lineage differs.')
    for old, new in zip(legacy['bones'], packet['bones']):
        _require(all(old[key] == new[key] for key in ('name', 'path', 'parent', 'humanRole', 'weighted', 'restSource'))
                 and len(old['rest']) == len(new['rest']) == 16
                 and all(_f32(a) == _f32(b) for a, b in zip(old['rest'], new['rest'])),
                 'The authenticated Rest/parent float32 lineage differs: ' + old['path'])
    _require(_f32(legacy['duration']) == _f32(packet['duration']), 'Legacy Body and input phase durations differ.')
    return native._digest([{k: b[k] for k in ('name', 'path', 'parent', 'humanRole', 'weighted', 'restSource')}
        | {'rest_f32': [_f32(v).hex() for v in b['rest']]} for b in legacy['bones']])


def _body(context, rig, clock):
    receipt = _read(clock['source_inputs']['snapshot_receipt'])
    _require(receipt['completed'] and receipt['metadata_preservation_verified'] and receipt['backup_verified'],
             'Use the actual successful original SaveCopy receipt.')
    recorded = receipt['after']['full_worklist']
    _require(recorded['known'] is True, 'The saved original worklist identity is Unknown.')
    saved = context.scene.character_designer_animation_worklist
    rows = recorded['value']['items']
    candidates = [(item, row) for item in saved.items for row in rows
        if item.item_id == row['item_id'] and item.work_item_id == row['work_item_id']
        and _same(item.rig, rig) and item.custom_action is not None and item.source_action is not None
        and item.custom_action.name == row['custom_action']['name']
        and item.source_action.name == row['source_action']['name']
        and item.custom_slot == row['custom_slot'] and item.source_slot == row['source_slot']]
    _require(len(candidates) == 1 and _same(saved.rig, rig)
             and saved.workspace_identity == recorded['value']['workspace_identity'],
             'The exact existing original Body row is missing, foreign or ambiguous.')
    item, row = candidates[0]
    _require(item.source_action.library is not None and item.custom_action.library is None
             and Path(bpy.path.abspath(item.source_action.library.filepath)).resolve() == Path(row['source_file']).resolve(),
             'The original Body Source must retain its actual linked cache.')
    _pin(row['source_file'], row['source_hash'])
    legacy = ua.load_package(clock['source_inputs']['packet']['path'])
    _require(legacy['_sha256'] == clock['source_inputs']['packet']['sha256'], 'The old Body packet changed.')
    for role, action, slot in (('BASE_SOURCE', item.source_action, item.source_slot), ('BASE_CUSTOM', item.custom_action, item.custom_slot)):
        proof = clock['selected_slot_proofs'][role]
        _require(proof['fingerprint_known'] is True and native._motion(action, slot) == proof['action_fingerprint']
                 and action.get(ua.PACKAGE_HASH_KEY) == legacy['_sha256'], 'The actual original Body content/slot changed: ' + role)
    return item, legacy, row, recorded['value']['workspace_identity']


def _pair_store(rig, pair):
    refs = {}; encoded = composition._encode(pair, refs)
    source = pair['source_action']
    source[PAIR_REFS] = refs
    source[PAIR_META] = json.dumps({'payload': encoded, 'sha256': native._digest(encoded)},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    rig[PAIR_KEY] = source


def _pair(rig):
    source = rig.get(PAIR_KEY)
    _require(isinstance(source, bpy.types.Action) and not source.library, 'The persisted Original input Source is missing.')
    row = json.loads(source[PAIR_META]); used = set()
    _require(set(row) == {'payload', 'sha256'} and native._digest(row['payload']) == row['sha256'], 'The Original pair envelope changed.')
    pair = composition._decode(row['payload'], source.get(PAIR_REFS, {}), used)
    _require(used == set(source.get(PAIR_REFS, {})) and _same(pair['rig'], rig) and _same(pair['source_action'], source),
             'The persisted Original pair native references changed.')
    return pair


def _reference_headers(table):
    return {str(k): {'type': type(v).__name__, 'name': v.name,
        'library': v.library.filepath if v.library else None} for k, v in table.items()}


def _properties_digest(action):
    # Read-only metadata, not recovery ownership. Existing Armature references
    # are represented by headers; no new native ID reference is stored.
    def readonly(value, depth=0):
        _require(depth <= 24, 'Body metadata exceeds its bounded depth.')
        if isinstance(value, bpy.types.ID):
            return {'id_header': _reference_headers({'value': value})['value']}
        if value is None or type(value) in (str, bool, int):
            return value
        if type(value) is float:
            _require(math.isfinite(value), 'Body metadata contains a nonfinite value.')
            return value
        if callable(getattr(value, 'items', None)):
            rows = list(value.items())
            _require(len(rows) <= 10000 and all(type(key) is str for key, _item in rows),
                     'Body metadata mapping is unbounded or has unsupported keys.')
            return {'mapping': {key: readonly(item, depth + 1) for key, item in rows}}
        if callable(getattr(value, 'to_list', None)):
            value = value.to_list()
        _require(isinstance(value, (tuple, list)) and len(value) <= 10000,
                 'Unsupported or unbounded Body metadata value.')
        return {'sequence': [readonly(item, depth + 1) for item in value]}
    return native._digest(readonly(dict(action.items())))


def _validate_pair_files(pair):
    for key in ('prepared_report', 'clock_certificate', 'snapshot_receipt', 'packet', 'legacy_packet', 'manifest', 'source_cache', 'prepared_model'):
        _pin(pair[key]['path'], pair[key]['sha256'])


def _pair_validate(pair, *, files=False):
    rig, source, custom = pair['rig'], pair['source_action'], pair['custom_action']
    _require(rig.type == 'ARMATURE' and not rig.library and not rig.data.library
             and rig.data.name == pair['data_name'] and _same(rig.data, target._load(rig)['motion_data'])
             and native._digest(rig[target.SESSION]) == pair['main_record_sha256']
             and _reference_headers(rig[target.REFS]) == pair['main_reference_headers'] and native._digest(ua._rest_state(rig.data)) == pair['rest_sha256']
             and native._rows(rig.matrix_world) == pair['object_matrix'], 'The original Rig/data/Rest changed.')
    _require(not source.library and not custom.library and not _same(source, custom)
             and composition._slot(source, pair['source_slot']) is not None
             and composition._slot(custom, pair['custom_slot']) is not None, 'The two Original input carriers changed.')
    _require(pair['schema'] == 'cdesigner.original-input-pair/1' and _same(rig.get(PAIR_KEY), source), 'The Original pair schema changed.')
    source_meta = json.loads(source[native._INPUT_META])
    _require(source_meta == pair['source_owner'] and source_meta['source_read_only'] is True
             and source_meta['new_action_id'] == pair['source_uuid']
             and pair['source_uuid'] not in (pair['capture']['newActionId'], pair['work_item_id'])
             and source_meta['capture_sha256'] == contract.capture_digest(pair['capture'])
             and source_meta['source_packet_sha256'] == pair['packet']['sha256']
             and source_meta['work_item_id'] == pair['work_item_id'] and source_meta['base_slot'] == pair['body_slot']
             and _same(source.get(native._INPUT_BASE), pair['body_action'])
             and native._motion(source, pair['source_slot']) == source_meta['source_input_motion'] == source_meta['initial_input_motion'],
             'The read-only twenty-channel Source owner or initial motion changed.')
    native._curves(source, pair['source_slot']); native._curves(custom, pair['custom_slot'])
    _require(source.get(ua.PACKAGE_HASH_KEY) == pair['packet']['sha256']
             and _same(source.get(native._INPUT_REF), custom)
             and json.loads(source[native.CAPTURE_KEY]) == pair['capture'], 'The input Source/Custom pairing changed.')
    custom_meta = json.loads(custom[native._INPUT_META])
    _require(custom_meta == pair['custom_owner'] and custom_meta['source_read_only'] is False
             and custom_meta['new_action_id'] == pair['capture']['newActionId']
             and _same(custom.get(native._INPUT_BASE), source), 'The editable Custom20 owner record changed.')
    saved = pair['scene'].character_designer_animation_worklist
    rows = [item for item in saved.items if item.item_id == pair['legacy_item_id']]
    _require(saved.workspace_identity == pair['workspace_identity'] and len(rows) == 1
             and _same(rows[0].custom_action, pair['body_action']) and rows[0].custom_slot == pair['body_slot']
             and _same(rows[0].source_action, pair['body_source']) and rows[0].source_slot == pair['body_source_slot']
             and rig.get('character_designer_animation_source_model_sha256') == pair['original_model_sha256'],
             'The original header/Body row/native ownership changed.')
    _require({k: getattr(rows[0], k) for k in pair['legacy_row_header']} == pair['legacy_row_header'], 'An old Body row identity/Link/cache metadata changed.')
    for action, slot, digest in ((pair['body_action'], pair['body_slot'], pair['body_motion']),
                                  (pair['body_source'], pair['body_source_slot'], pair['body_source_motion'])):
        _require(composition._slot(action, slot) is not None and native._motion(action, slot) == digest
                 and action.get(ua.PACKAGE_HASH_KEY) == pair['legacy_packet']['sha256']
                 and _properties_digest(action) == pair['body_properties'][action.name]
                 and action.use_fake_user == pair['body_fake_user'][action.name], 'An old Body reference Action/slot/content/tag changed.')
    if files:
        _validate_pair_files(pair)
    return pair


def validate_session(session, *, files=False):
    pair = _pair_validate(session['original_inputs'], files=files)
    _require(_same(session['rig'], pair['rig']) and _same(session['scene'], pair['scene'])
             and _same(session['base_action'], pair['source_action']) and session['base_slot'] == pair['source_slot']
             and _same(session['input_action'], pair['custom_action']) and session['input_slot'] == pair['custom_slot']
             and session['capture'] == pair['capture'] and session['work_item_id'] == pair['work_item_id']
             and session['source_packet_sha256'] == pair['packet']['sha256'] and not session['source_read_only']
             and not (session['edit_role'] == 'BASE' and session['tweak_enabled']),
             'Original Body is read-only; only its independent Custom20 may be edited.')
    return pair


def validate_reference(session, *, files=False, sealed=True):
    if files: validate_session(session, files=True)
    pair, reference = session['original_inputs'], session['preview_reference']
    _require(_same(session['base_action'], pair['source_action']) and _same(session['input_action'], pair['custom_action']), 'The reference belongs to another owned input pair.')
    side = reference['legacy_side']
    expected = (pair['body_source'], pair['body_source_slot'], pair['body_source_motion']) if side == 'SOURCE' else (pair['body_action'], pair['body_slot'], pair['body_motion'])
    _require(side in {'SOURCE', 'CUSTOM'} and _same(reference['action'], expected[0]) and reference['slot'] == expected[1]
             and reference['motion_sha256'] == expected[2] and reference['sample_rate'] == pair['body_clock']
             and reference['duration'] == pair['body_duration'] and reference['start'] == pair['body_start']
             and reference['certificate_sha256'] == pair['clock_certificate']['sha256'], 'The read-only legacy Body preview/reference clock changed.')


def preview_base_timing(session, reference):
    validate_reference(session)
    reference = reference or session['preview_reference']
    rate = session['scene_clock'][0] / session['scene_clock'][1]
    duration, source_duration = session['capture']['duration'], reference['duration']
    _require(rate > 0 and _f32(duration) == _f32(source_duration), 'The independent legacy Body phase clock changed.')
    return reference['action'], reference['slot'], {'begin': reference['start'],
        'end': reference['start'] + source_duration * reference['sample_rate'], 'scene_start': session['scene_start'],
        'scene_end': session['scene_start'] + duration * rate, 'scale': duration * rate / (source_duration * reference['sample_rate'])}


def _reference(pair, side):
    action, slot, motion = ((pair['body_source'], pair['body_source_slot'], pair['body_source_motion']) if side == 'SOURCE'
        else (pair['body_action'], pair['body_slot'], pair['body_motion']))
    return {'action': action, 'slot': slot, 'motion_sha256': motion, 'sample_rate': pair['body_clock'],
        'duration': pair['body_duration'], 'start': pair['body_start'], 'legacy_side': side,
        'certificate_sha256': pair['clock_certificate']['sha256']}


def _session(context, pair):
    custom = pair['custom_action']
    return {'version': 1, 'owner': str(uuid.uuid4()), 'rig': pair['rig'], 'scene': pair['scene'], 'capture': pair['capture'],
        'base_action': pair['source_action'], 'base_slot': pair['source_slot'], 'input_action': custom, 'input_slot': pair['custom_slot'],
        'new_action_id': pair['capture']['newActionId'], 'work_item_id': pair['work_item_id'], 'source_packet_sha256': pair['packet']['sha256'],
        'source_read_only': False, 'input_owner': json.loads(custom[native._INPUT_META]), 'native_bindings': pair['native_bindings'],
        'conversion': pair['conversion'], 'data_name': pair['rig'].data.name, 'rest_sha256': pair['rest_sha256'],
        'object_matrix': pair['object_matrix'], 'prior': composition._prior(context, pair['rig']), 'display': bone_display._snapshot(pair['rig']),
        'tracks': {}, 'enabled': False, 'edit_role': 'BASE', 'tweak_enabled': False, 'properties_before': {}, 'committed': True,
        'original_inputs': pair, 'preview_reference': _reference(pair, 'CUSTOM')}


def _start(context, pair):
    pointer = pair['rig'].as_pointer()
    _require(pointer not in native._BUSY and not native._SUSPENDED.get(pointer, 0),
             'Start Original20 inputs outside an existing owned preview transaction.')
    session = _session(context, pair)
    from . import forearm_twist
    with forearm_twist.defer_runtime(context):
        try:
            was_busy = pointer in native._BUSY
            native._BUSY.add(pointer)
            try:
                native._store(pair['rig'], session)
                if pair['capture'].get('weaponPreview') is not None:
                    from . import animation_composition_weapon as weapon
                    packet = ua.load_package(pair['packet']['path'])
                    mapping = ua._mapping(pair['rig'], packet, context.scene.unit_settings.scale_length)
                    session['weapon_preview'] = weapon.add_rigging_context(context, pair['rig'], pair['capture']['weaponPreview'], packet, mapping, session=session)
                    native._store(pair['rig'], session)
            finally:
                if not was_busy:
                    native._BUSY.discard(pointer)
            if pair['capture']['previewEnabled']: native.activate(context, session)
            return session
        except Exception as primary:
            errors = []
            if 'weapon_preview' in session:
                try:
                    from . import animation_composition_weapon as weapon
                    if weapon.RECORD in pair['rig']: weapon.remove_rigging_context(context, pair['rig'], session=session)
                    session.pop('weapon_preview', None)
                except Exception as error: errors.append('new owned weapon display: ' + str(error))
            for label, operation in (
                ('new owned session', lambda: native.restore_owned(context, pair['rig']) if native.SESSION_KEY in pair['rig'] else None),
                ('original native state', lambda: composition._restore_prior(context, pair['rig'], session['prior'])),
                ('original display', lambda: bone_display._restore(pair['rig'], session['display']))):
                try: operation()
                except Exception as error: errors.append(label + ': ' + str(error))
            if errors: primary.add_note('Original20 activation rollback: ' + '; '.join(errors))
            raise


def import_inputs(context, rig, prepared_report_file, prepared_report_sha256, clock_certificate_file, clock_certificate_sha256):
    """Explicit Original20 setup; requires actual completed clock/content proof."""
    _require(native.current(rig) is None and not composition.active(rig), 'Close the current owned edit preview first.')
    _require(context.scene.objects.get(rig.name) == rig and context.active_object == rig and target.managed(rig) and target.is_adopted(rig),
             'Use the authenticated existing Main Rig in its actual scene.')
    target.ensure_active(context, rig)
    prepared_pin, clock_pin = _pin(prepared_report_file, prepared_report_sha256), _pin(clock_certificate_file, clock_certificate_sha256)
    prepared, clock = _read(prepared_pin), _read(clock_pin)
    _require(prepared['schema'] == 'randomrealm.two-arm-real-prepare/1' and prepared['passed'] is True and prepared['error'] is None,
             'Use the genuine completed typed Prepare report.')
    _require(clock['schema'] == 'x.validation.original-legacy-base-clock/1' and clock['completed'] is True
             and clock['native_execution_performed'] is True and clock['legacy_clock_known'] is True
             and all(clock[k] is True for k in ('context_and_pose_restored', 'raw_asset_data_unchanged', 'input_files_unchanged')),
             'The actual legacy clock/content certificate is incomplete or Unknown.')
    item, legacy, old_row, workspace_identity = _body(context, rig, clock)
    packet = ua.load_package(prepared['packetPath']); packet_pin = _pin(prepared['packetPath'], prepared['packetSha256'])
    capture = contract.validate_capture(packet['rigging'], packet=packet)
    work_id = native._uid(prepared['realWorkItemId'])
    _require(packet['_sha256'] == packet_pin['sha256'] and capture['targetGuid'] == prepared['targetGuid']
             and capture['newActionId'] == prepared['targetControlActionId'] and capture['bindingsSha256'] == prepared['bindingsSha256']
             and work_id != capture['newActionId'], 'The actual prepared WorkID/Target input UUID/bindings changed.')
    lineage = _lineage(legacy, packet)
    identity = json.loads(workspace_identity) if isinstance(workspace_identity, str) else workspace_identity
    _require(identity[1]['avatarHash'] == capture['avatarDependencyHash'] and rig.get('character_designer_animation_source_model_sha256') == old_row['model_sha256'],
             'The original Avatar/model owner is not the authenticated character lineage.')
    manifest_pin = _pin(prepared['manifestPath'], prepared['manifestSha256']); manifest = _read(manifest_pin)
    _require(manifest['schema'] == 'randomrealm.animation-link/1' and native._uid(manifest['workItemId']) == work_id
             and manifest['targetGuid'] == capture['targetGuid'] and manifest['sourcePackageSha256'] == packet_pin['sha256']
             and Path(manifest['sourcePackage']).resolve() == Path(packet_pin['path']) and manifest['modelSha256'] == prepared['modelSha256'],
             'The genuine prepared typed Link identity changed.')
    mapping = ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
    bindings = native._binding_plan(rig, packet, capture, mapping)
    if PAIR_KEY in rig:
        pair = _pair_validate(_pair(rig), files=True)
        _require(pair['prepared_report'] == prepared_pin and pair['clock_certificate'] == clock_pin, 'Preserve the existing pair; do not overwrite another prepared work.')
        return _start(context, pair)
    pair = None; actions = []; session = None
    prior, display = composition._prior(context, rig), bone_display._snapshot(rig)
    try:
        carriers = [json.loads(a[native._INPUT_META]) for a in bpy.data.actions if native._INPUT_META in a]
        _require(not any(a.get('new_action_id') == capture['newActionId'] for a in carriers), 'These prepared input controls already have an owner; nothing was replaced.')
        source_uuid = uuid.uuid4().hex
        _require(source_uuid not in (capture['newActionId'], work_id) and not any(a.get('new_action_id') == source_uuid for a in carriers), 'Source20 needs its own independent UUID.')
        source, source_slot, source_owner = native._new_input_action(rig, capture, item.custom_action, item.custom_slot,
            True, packet_pin['sha256'], work_id, source_action_id=source_uuid)
        actions.append(source)
        for key, value in ((ua.PACKAGE_KEY, packet_pin['path']), (ua.PACKAGE_HASH_KEY, packet_pin['sha256']),
            (ua.NATIVE_TIMEBASE_KEY, capture['sampleRate']), ('unity_sample_rate', capture['sampleRate']),
            ('unity_start_frame', 1.), ('unity_duration', capture['duration'])): source[key] = value
        custom, custom_slot, custom_owner = native._new_input_action(rig, capture, source, source_slot, False, packet_pin['sha256'], work_id)
        actions.append(custom)
        source[native.CAPTURE_KEY] = json.dumps(capture, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        source[native._INPUT_REF] = custom
        pair = {'schema': 'cdesigner.original-input-pair/1', 'rig': rig, 'scene': context.scene, 'data_name': rig.data.name,
            'source_action': source, 'source_slot': source_slot, 'source_uuid': source_uuid, 'source_owner': source_owner,
            'custom_action': custom, 'custom_slot': custom_slot, 'custom_owner': custom_owner, 'capture': capture, 'work_item_id': work_id,
            'body_action': item.custom_action, 'body_slot': item.custom_slot, 'body_source': item.source_action, 'body_source_slot': item.source_slot,
            'body_properties': {a.name: _properties_digest(a) for a in (item.custom_action, item.source_action)},
            'body_fake_user': {a.name: a.use_fake_user for a in (item.custom_action, item.source_action)},
            'body_motion': native._motion(item.custom_action, item.custom_slot), 'body_source_motion': native._motion(item.source_action, item.source_slot),
            'body_clock': clock['verified_legacy_action_rate'], 'body_start': float(item.source_action.get('unity_start_frame', 1.)),
            'body_duration': legacy['duration'], 'prepared_report': prepared_pin, 'clock_certificate': clock_pin,
            'snapshot_receipt': clock['source_inputs']['snapshot_receipt'], 'packet': packet_pin,
            'legacy_packet': clock['source_inputs']['packet'], 'manifest': manifest_pin,
            'source_cache': _pin(old_row['source_file'], old_row['source_hash']), 'prepared_model': _pin(prepared['modelFile'], prepared['modelSha256']),
            'legacy_row_header': {k: getattr(item, k) for k in ('item_id', 'work_item_id', 'clip_key', 'manifest_path', 'source_file', 'source_hash', 'model_file', 'model_sha256', 'link_identity', 'source_slot', 'custom_slot')},
            'original_model_sha256': old_row['model_sha256'], 'workspace_identity': workspace_identity, 'legacy_item_id': item.item_id,
            'main_record_sha256': native._digest(rig[target.SESSION]), 'main_reference_headers': _reference_headers(rig[target.REFS]), 'rest_lineage_sha256': lineage, 'rest_sha256': native._digest(ua._rest_state(rig.data)), 'object_matrix': native._rows(rig.matrix_world),
            'native_bindings': bindings, 'conversion': native._rows(mapping.conversion)}
        _require(pair['body_clock'] == 30. and pair['body_start'] == 1., 'Only the actually certified stored Body clock is supported.')
        _pair_store(rig, pair); return _start(context, pair)
    except Exception as error:
        cleanup = []
        for label, operation in (
            ('new owned session', lambda: native.restore_owned(context, rig) if native.SESSION_KEY in rig else None),
            ('original native state', lambda: composition._restore_prior(context, rig, prior)),
            ('original display', lambda: bone_display._restore(rig, display))):
            try: operation()
            except Exception as failure: cleanup.append(label + ': ' + str(failure))
        rig.pop(PAIR_KEY, None)
        for action in actions:
            action.pop(PAIR_REFS, None); action.pop(native._INPUT_REF, None); action.pop(native._INPUT_BASE, None)
        for action in reversed(actions):
            try:
                _require(action.users == int(action.use_fake_user), 'A failed new carrier acquired a foreign user; retained.')
                action.use_fake_user = False; bpy.data.actions.remove(action)
            except Exception as failure: cleanup.append(str(failure))
        if cleanup: error.add_note('Original20 creation rollback: ' + '; '.join(cleanup))
        raise


def current(rig):
    session = native.current(rig)
    if session is not None:
        _require('original_inputs' in session, 'Another ordinary Rigging owner is active.')
        # native.current already authenticated the complete pair synchronously.
        # Supplement its live checks with file pins, without rehashing Body twice.
        _validate_pair_files(session['original_inputs'])
    return session


def display_state(rig):
    row = native.display_state(rig); cached = native._CACHE.get(rig.as_pointer()) if rig else None
    if row is None or cached is None or 'original_inputs' not in cached: return None
    return {**row, 'base_slot': cached['base_slot'], 'input_slot': cached['input_slot'],
        'legacy_side': cached['preview_reference']['legacy_side']}


def _active(context, rig):
    session = current(rig)
    if session is None:
        pair = _pair_validate(_pair(rig), files=True)
        _require(_same(context.scene, pair['scene']), 'Return to the original owning scene.')
        session = _start(context, pair)
    return session


def edit_controls(context, rig):
    return native.activate(context, _active(context, rig), edit_role='RIGGING', enabled=True)


def key_current(context, rig):
    return native.key_current(context, _active(context, rig))


def view_body(context, rig, side='CUSTOM'):
    _require(side in {'SOURCE', 'CUSTOM'}, 'Choose the exact old Body Source or Custom reference.')
    pointer = rig.as_pointer()
    _require(pointer not in native._BUSY and not native._SUSPENDED.get(pointer, 0),
             'Switch Body references outside an active preview transaction.')
    session = _active(context, rig)
    _require(context.scene is session['scene'], 'Activate Rigging in its saved owning scene.')
    before, prior = native._detached(session), composition._prior(context, rig)
    display = bone_display._snapshot(rig)
    try:
        # An authenticated BASE stack already owns its strips. Replace the
        # reference before evaluating it; do not evaluate the old Body again.
        if not session['tracks'] or session['edit_role'] != 'BASE' or session['tweak_enabled']:
            native.activate(context, session, edit_role='BASE', enabled=False)
        native._BUSY.add(pointer)
        track = native._tracks(session)['BASE']
        session['preview_reference'] = _reference(session['original_inputs'], side)
        action, slot, timing = preview_base_timing(session, session['preview_reference'])
        native._assign_preview_base(track.strips[0], action, slot, timing)
        session['tracks']['BASE'].update(action=action, slot=slot, fields={k: composition._plain(getattr(track.strips[0], k)) for k in composition._STRIP_FIELDS})
        native._store(rig, session)
        return native.activate(context, session, edit_role='BASE', enabled=False)
    except Exception as primary:
        errors = []; native._BUSY.add(pointer)
        try:
            if rig.animation_data and rig.animation_data.use_tweak_mode:
                composition._tweak(context, rig, None, enabled=False)
        except Exception as error: errors.append('leave failed owned Tweak: ' + str(error))
        try:
            old = before['tracks'].get('BASE')
            if old:
                track = rig.animation_data.nla_tracks.get(old['name'])
                _require(track is not None and len(track.strips) == 1, 'The original Body reference strip disappeared.')
                strip = track.strips[0]
                strip.action, strip.action_slot = old['action'], composition._slot(old['action'], old['slot'])
                for field, value in old['fields'].items():
                    if field not in {'frame_start', 'frame_end'}: setattr(strip, field, value)
                strip.frame_start, strip.frame_end = old['fields']['frame_start'], old['fields']['frame_end']
            else:
                for row in session['tracks'].values():
                    created = rig.animation_data.nla_tracks.get(row['name'])
                    _require(created is not None and len(created.strips) == 1
                             and created.strips[0].action in (session['input_action'], session['original_inputs']['body_action'], session['original_inputs']['body_source']),
                             'A newly-created preview track changed; it was retained.')
                    rig.animation_data.nla_tracks.remove(created)
                for key, value in session['properties_before'].items():
                    if value['present']: rig[key] = value['value']
                    else: rig.pop(key, None)
        except Exception as error: errors.append('old Body strip: ' + str(error))
        session.clear(); session.update(before)
        for label, operation in (('prior native state', lambda: composition._restore_prior(context, rig, prior)),
                                  ('prior display', lambda: bone_display._restore(rig, display))):
            try: operation()
            except Exception as error: errors.append(label + ': ' + str(error))
        try:
            if before['tweak_enabled']:
                composition._tweak(context, rig, native._tracks(session)[before['edit_role']], enabled=True)
            native._restore_live_pose(rig, prior)
        except Exception as error: errors.append('original edit role/Tweak/live pose: ' + str(error))
        try: native._store(rig, session)
        except Exception as error: errors.append('persistent owner: ' + str(error))
        if errors: primary.add_note('Read-only Body view recovery: ' + '; '.join(errors))
        raise
    finally: native._BUSY.discard(pointer)


def capture_inputs(context, session):
    pair = session['original_inputs']
    # Native capture authenticates the complete live pair through _validate.
    # Retain all file pins before returning, without scanning dense Body twice.
    dto = native.capture_inputs(context, session, source_packet_sha256=pair['packet']['sha256'], work_item_id=pair['work_item_id'],
        source_action=pair['source_action'], source_action_slot=pair['source_slot'])
    _validate_pair_files(pair)
    return dto


def publish_inputs(context, rig, filepath):
    session = _active(context, rig)
    dto = capture_inputs(context, session)
    pair = session['original_inputs']
    result_path = Path(filepath).resolve(); publication_path = result_path.with_name(result_path.stem + '.publication.json')
    _require(result_path.suffix.lower() == '.json' and result_path.parent.is_dir() and not result_path.exists() and not publication_path.exists(),
             'Choose two new owned JSON output files; never overwrite original/prepared evidence.')
    payload = (json.dumps(dto, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    publication = {'schema': PUBLICATION_SCHEMA, 'completed': True,
        'sourceActionId': pair['source_uuid'], 'targetInputActionId': pair['capture']['newActionId'], 'prepareReportPath': pair['prepared_report']['path'], 'prepareReportSha256': pair['prepared_report']['sha256'],
        'returnFile': str(result_path), 'returnSha256': hashlib.sha256(payload).hexdigest(),
        **{k: dto[k] for k in ('sourcePacketSha256', 'bindingsSha256', 'targetGuid', 'workItemId', 'newActionId',
            'sourceAction', 'sourceActionSlot', 'targetInputAction', 'targetInputActionSlot', 'duration', 'sampleRate', 'loopTime')},
        'sourceReadOnly': True, 'targetEditable': True, 'sourceControlsOnly': True, 'targetControlsOnly': True, 'controlChannelCount': 20,
        **{k: pair['capture'][k] for k in ('targetDependencyHash', 'targetLayoutSha256', 'baseControllerGuid', 'baseControllerDependencyHash', 'avatarDependencyHash')},
        'sourceTimeMode': pair['capture']['sourceTimeMode'], 'legacyBodyReference': {
            'action': pair['body_action'].name, 'slot': pair['body_slot'], 'sourceAction': pair['body_source'].name, 'sourceSlot': pair['body_source_slot'],
            'storedFrameRate': pair['body_clock'], 'startFrame': pair['body_start'], 'duration': pair['body_duration'],
            'clockCertificatePath': pair['clock_certificate']['path'], 'clockCertificateSha256': pair['clock_certificate']['sha256'],
            'timeSemantics': 'normalized-base-phase; independent legacy stored Body clock; no Body return', 'modelEquivalenceVerified': False}}
    pub_bytes = (json.dumps(publication, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    created_return = created_publication = False
    try:
        with result_path.open('xb') as stream:
            created_return = True; stream.write(payload); stream.flush()
        _pin(result_path, publication['returnSha256'])
        with publication_path.open('xb') as stream:
            created_publication = True; stream.write(pub_bytes); stream.flush()
        _pin(publication_path, hashlib.sha256(pub_bytes).hexdigest())
    except Exception as primary:
        for path, created, digest in ((publication_path, created_publication, hashlib.sha256(pub_bytes).hexdigest()),
                                      (result_path, created_return, publication['returnSha256'])):
            if not created: continue
            try:
                if path.is_file() and _sha(path) == digest: path.unlink()
                elif path.exists(): primary.add_note('Incomplete or changed owned output retained: ' + str(path))
            except Exception as error: primary.add_note('Owned output cleanup: ' + str(error))
        raise
    return {'return': _pin(result_path, publication['returnSha256']),
        'publication': _pin(publication_path, hashlib.sha256(pub_bytes).hexdigest())}


def close_owned(context, rig):
    session = current(rig)
    if session is not None: native.restore_owned(context, rig)
