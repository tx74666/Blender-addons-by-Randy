"""Read-only Direct refusal, independent of surface export services.

Receipts bind a host reverse-link audit to one native animation snapshot. They
never authorize physics export or infer absence from an empty worker Scene.
No depsgraph, pose or ID writes are performed here.
"""
import hashlib
import json
from pathlib import Path

SCHEMA = 'cdesigner.direct-dress-admission.v1'
JOB_KEY = 'direct_dress_admission'
RECORD_KEY = 'character_designer_skirt_v1'
OWNER_KEY = 'character_designer_skirt_owner'
RIG_KEY = 'character_designer_skirt_armature'
SOURCE_KEY = 'character_designer_skirt_source'
SHARED_KEY = 'character_designer_shared_skirts_v1'
STATE_KEY = 'character_designer_dress_direct_state_v1'
ROLE_KEY = 'character_designer_skirt_surface_role'
DIRECT = 'DIRECT_MAIN_CLOTH_V1'
KNOWN = frozenset({'LEGACY_CAGE', 'ACTUAL_SURFACE_DELTA_V1'})


def _require(condition, message):
    if not condition:
        raise ValueError('Direct Dress export boundary: ' + message)


def _text(value):
    return type(value) is str and bool(value)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate saved metadata field.')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Direct Dress export boundary: non-finite saved metadata.')


def _direct_marker(source):
    if STATE_KEY in source or source.get(ROLE_KEY) == 'INPUT_SURFACE':
        return True
    for modifier in source.modifiers:
        group = getattr(modifier, 'node_group', None)
        if group is not None and group.get(ROLE_KEY) in {'DIRECT_NODE_GROUP', 'BODY_NODE_GROUP'}:
            return True
    return False


def _record(source):
    if _direct_marker(source):
        raise ValueError(source.name + ': Direct Dress export validation is pending; preserve its editable source and vertex physics.')
    if RECORD_KEY not in source:
        return None
    raw = source[RECORD_KEY]
    _require(type(raw) is str, source.name + ': saved Dress record must be JSON text.')
    try:
        record = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    except (ValueError, TypeError) as error:
        raise ValueError(source.name + ': restore the saved Dress record before export.') from error
    _require(type(record) is dict, source.name + ': saved Dress record must be an object.')
    physics = record.get('physics')
    _require(physics is None or type(physics) is dict, source.name + ': unknown Dress physics metadata.')
    backend = physics.get('backend', 'LEGACY_CAGE') if physics is not None else 'LEGACY_CAGE'
    if backend == DIRECT:
        raise ValueError(source.name + ': Direct Dress export validation is pending; baked vertex physics is not bone-only animation.')
    _require(type(backend) is str and backend in KNOWN, source.name + ': unknown Dress backend.')
    _require(type(record.get('version')) is int and record['version'] == 1 and _text(record.get('owner')),
             source.name + ': incomplete saved Dress ownership.')
    if OWNER_KEY in source:
        _require(source[OWNER_KEY] == record['owner'], source.name + ': Dress owner differs from saved metadata.')
    return record, backend, hashlib.sha256(raw.encode('utf-8')).hexdigest()


def reject_model_sources(objects):
    for source in objects:
        if getattr(source, 'type', None) == 'MESH':
            _record(source)


def reject_model_snapshot(job, objects):
    names = job.get('objects')
    _require(type(names) is list and all(_text(name) for name in names)
             and len(names) == len(set(names)), 'missing or ambiguous model snapshot inventory.')
    selected = [objects.get(name) for name in names]
    _require(all(obj is not None for obj in selected), 'model snapshot objects are missing.')
    reject_model_sources(selected)


def _identity(rig, action, slot_handle):
    _require(getattr(rig, 'type', None) == 'ARMATURE' and _text(rig.name) and _text(rig.data.name),
             'selected native Rig identity is missing.')
    _require(action is not None and _text(action.name) and type(slot_handle) is int,
             'selected Action/slot identity is missing.')
    slots = [slot for slot in action.slots if slot.handle == slot_handle]
    _require(len(slots) == 1 and slots[0].target_id_type == 'OBJECT', 'selected Action slot is missing or changed.')
    return {'rig': {'name': rig.name, 'data_name': rig.data.name, 'type': rig.type},
            'action': {'name': action.name, 'slot_handle': slot_handle, 'slot_target_type': 'OBJECT'}}


def _markers(rig, objects):
    sources, shared, bones = [], [], []

    def source_reference(source):
        _require(getattr(source, 'type', None) == 'MESH' and source in objects,
                 'a native Dress source pointer is missing from the snapshot.')
        if source not in sources:
            sources.append(source)
        return source.name

    if SHARED_KEY in rig:
        group = rig[SHARED_KEY]
        _require(hasattr(group, 'items'), 'shared Dress registry is not a native mapping.')
        for owner, source in group.items():
            _require(_text(owner), 'shared Dress owner is missing.')
            shared.append({'owner': owner, 'source': source_reference(source)})
    legacy = source_reference(rig[SOURCE_KEY]) if SOURCE_KEY in rig else None
    for bone in rig.data.bones:
        if OWNER_KEY in bone or SOURCE_KEY in bone:
            owner = bone.get(OWNER_KEY)
            _require(_text(owner) and SOURCE_KEY in bone, 'a Dress bone has incomplete owner/source markers.')
            bones.append({'bone': bone.name, 'owner': owner, 'source': source_reference(bone[SOURCE_KEY])})
    return sources, {'shared_sources': sorted(shared, key=lambda item: item['owner']),
                     'legacy_source': legacy, 'owned_bones': sorted(bones, key=lambda item: item['bone'])}


def animation_host_admission(objects, rig, action, slot_handle):
    """Audit all data objects, including other Scenes' reverse links."""
    identity = _identity(rig, action, slot_handle)
    objects = list(objects)
    sources, markers = _markers(rig, objects)
    for source in objects:
        if getattr(source, 'type', None) != 'MESH':
            continue
        # Tagged non-source Legacy/Delta physics helpers have RIG_KEY too.
        # Registry/Bone pointers above still catch a source with a lost record.
        if RECORD_KEY not in source and not _direct_marker(source):
            continue
        saved_rig_name = None
        raw = source.get(RECORD_KEY)
        if type(raw) is str:
            try:
                value = json.loads(raw)
                saved_rig_name = value.get('rig') if type(value) is dict else None
            except (ValueError, TypeError):
                pass  # Relevant native links below still require valid metadata.
        belongs = (source.get(RIG_KEY) == rig or saved_rig_name == rig.name
                   or (RECORD_KEY in source or STATE_KEY in source) and any(
                       modifier.type == 'ARMATURE' and modifier.object == rig for modifier in source.modifiers))
        if belongs and source not in sources:
            sources.append(source)
    inventory = []
    for source in sorted(sources, key=lambda item: item.name):
        parsed = _record(source)
        _require(parsed is not None and source.get(RIG_KEY) == rig,
                 source.name + ': native Dress/Rig ownership is incomplete.')
        record, backend, digest = parsed
        inventory.append({'name': source.name, 'type': source.type, 'owner': record['owner'],
                          'rig_name': rig.name, 'record_sha256': digest, 'backend': backend,
                          'direct_state_present': False})
    owners = {item['owner']: item['name'] for item in inventory}
    _require(len(owners) == len(inventory), 'Dress source owners are ambiguous.')
    for marker in markers['shared_sources'] + markers['owned_bones']:
        _require(owners.get(marker['owner']) == marker['source'], 'native Dress source/owner markers differ.')
    if markers['legacy_source'] is not None:
        _require(markers['legacy_source'] in {item['name'] for item in inventory}, 'legacy Dress source is unaccounted for.')
    return {'schema': SCHEMA, 'version': 1, 'scope': 'SKELETAL_ACTION_DIRECT_REFUSAL_ONLY',
            'complete_host_reverse_audit': True, **identity, 'source_count': len(inventory),
            'sources': inventory, 'rig_markers': markers, 'physics_export_authorized': False}


def _snapshot_identity(snapshot):
    path = Path(snapshot)
    _require(path.is_absolute() and path.is_file(), 'the exact native snapshot file is missing.')
    path = path.resolve()
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    _require(before.st_size > 0 and (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
             'snapshot changed while its admission receipt was bound.')
    return {'path': str(path), 'sha256': digest.hexdigest(), 'bytes': before.st_size}


def bind_animation_snapshot(admission, snapshot):
    result = json.loads(json.dumps(admission, allow_nan=False))
    _require(type(result) is dict and result.get('schema') == SCHEMA, 'host Direct admission receipt is missing.')
    result['snapshot'] = _snapshot_identity(snapshot)
    return result


def verify_animation_snapshot(job, objects, rig, action, snapshot):
    """A missing receipt is Unknown, including a Rig/Action-only snapshot."""
    receipt = job.get(JOB_KEY)
    _require(type(receipt) is dict and type(job.get('action_slot')) is int,
             'a complete new host Direct admission receipt is required.')
    _require(job.get('rig') == getattr(rig, 'name', None) and job.get('action') == getattr(action, 'name', None),
             'animation job identity differs from the native snapshot.')
    expected = bind_animation_snapshot(animation_host_admission(objects, rig, action, job['action_slot']), snapshot)
    try:
        actual_json = json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False)
        expected_json = json.dumps(expected, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError('Direct Dress export boundary: admission receipt is not finite typed JSON.') from error
    _require(actual_json == expected_json, 'host reverse inventory, native source markers or snapshot receipt differs.')
