"""One explicit Action association and atomic publication of its Unity Link.

FBX/metadata revisions are immutable. Only the small, owned manifest advances;
no watcher, library, scene save, or Unity mutation belongs to this module.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid


SCHEMA = 'randomrealm.animation-link/1'
MANIFEST_NAME = 'character_animation_link.json'
LINK_KEY = 'character_designer_animation_link'
ACTION_KEY = LINK_KEY + '_action'
RIG_ID_KEY = LINK_KEY + '_rig_id'
PACKAGE_KEY = 'character_designer_unity_animation_package'
PACKAGE_HASH_KEY = PACKAGE_KEY + '_sha256'
IDENTITY_FIELDS = ('schema', 'linkId', 'targetGuid', 'clipGuid', 'clipLocalId',
                   'sourcePackage', 'sourcePackageSha256')
OPTIONAL_IDENTITY_FIELDS = ('modelFile', 'modelSha256')
OUTPUT_FIELDS = frozenset(('blendFile', 'sourceAction', 'fbxFile', 'fbxSha256',
                           'metadataFile', 'metadataSha256'))


class AnimationLinkError(ValueError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _absolute(value, label, *, resolve=True):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise AnimationLinkError(label + ' must be an absolute path.')
    return Path(value).resolve() if resolve else Path(value)


def _json(raw, label):
    try:
        value = json.loads(raw, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise AnimationLinkError(label + ' is not valid JSON.') from exc
    if not isinstance(value, dict):
        raise AnimationLinkError(label + ' must be a JSON object.')
    return value


def _validate_identity(record):
    if record.get('schema') != SCHEMA:
        raise AnimationLinkError('Unsupported animation Link schema.')
    try:
        uuid.UUID(record['linkId'])
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise AnimationLinkError('The animation Link has no valid linkId.') from exc
    for field in ('targetGuid', 'clipGuid'):
        if not isinstance(record.get(field), str) or not re.fullmatch(r'[0-9a-fA-F]{32}', record[field]):
            raise AnimationLinkError('The animation Link has an invalid ' + field + '.')
    local_id = record.get('clipLocalId')
    if type(local_id) is not int or local_id == 0 or not -(2**63) <= local_id < 2**63:
        raise AnimationLinkError('clipLocalId must be an exact nonzero signed 64-bit integer.')
    if not isinstance(record.get('sourcePackageSha256'), str) or not re.fullmatch(
            r'[0-9a-fA-F]{64}', record['sourcePackageSha256']):
        raise AnimationLinkError('The animation Link has no valid source packet hash.')
    _absolute(record.get('sourcePackage'), 'sourcePackage', resolve=False)
    for field in OPTIONAL_IDENTITY_FIELDS:
        if field in record and record[field] is not None and not isinstance(record[field], str):
            raise AnimationLinkError('The animation Link has an invalid ' + field + '.')
    if record.get('modelFile'):
        _absolute(record['modelFile'], 'modelFile', resolve=False)
    if record.get('modelSha256') and not re.fullmatch(r'[0-9a-fA-F]{64}', record['modelSha256']):
        raise AnimationLinkError('The animation Link has an invalid modelSha256.')


def _identity(record):
    _validate_identity(record)
    model = record.get('modelFile')
    model_hash = record.get('modelSha256')
    return (record['schema'], uuid.UUID(record['linkId']).hex,
            record['targetGuid'].lower(), record['clipGuid'].lower(), record['clipLocalId'],
            os.path.normcase(str(_absolute(record['sourcePackage'], 'sourcePackage'))),
            record['sourcePackageSha256'].lower(),
            'modelFile' in record, os.path.normcase(str(_absolute(model, 'modelFile'))) if model else model,
            'modelSha256' in record, model_hash.lower() if model_hash else model_hash)


def _read_link(path):
    path = Path(path).resolve()
    if path.name != MANIFEST_NAME:
        raise AnimationLinkError('Choose ' + MANIFEST_NAME + ' beside the Unity packet.')
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise AnimationLinkError('The animation Link is missing or too large.')
    raw = path.read_bytes()
    record = _json(raw, 'Animation Link')
    _validate_identity(record)
    if _absolute(record['sourcePackage'], 'sourcePackage').parent != path.parent:
        raise AnimationLinkError('The Link and its source packet must be in the same folder.')
    return record, raw


def _verify_source(record):
    source = _absolute(record['sourcePackage'], 'sourcePackage')
    if not source.is_file() or _sha256(source) != record['sourcePackageSha256'].lower():
        raise AnimationLinkError('The linked Unity packet changed or is missing. Import and bind the current source again.')


def load_link(path):
    """Read the packet-adjacent contract, preserving all unknown fields."""
    record, _ = _read_link(path)
    _verify_source(record)
    return {**record, '_manifest_path': str(Path(path).resolve())}


def get_link(rig):
    """Return the saved rig association without filesystem I/O, for UI draw."""
    if rig is None or LINK_KEY not in rig:
        return None
    record = _json(rig[LINK_KEY], 'Saved Action association')
    if record.get('version') != 1:
        raise AnimationLinkError('The saved Action association has an unsupported version.')
    if not isinstance(record.get('identity'), dict):
        raise AnimationLinkError('The saved Action association lost its source identity.')
    _validate_identity(record['identity'])
    if record.get('rig_id') != rig.get(RIG_ID_KEY):
        raise AnimationLinkError('The saved Action association belongs to another rig.')
    _absolute(record.get('manifest_path'), 'manifest_path', resolve=False)
    record['link_id'] = record['identity']['linkId']
    return record


def _objects():
    import bpy
    return bpy.data.objects


def _check_duplicates(rig, rig_id, link_id):
    for other in _objects():
        if other is rig or getattr(other, 'type', None) != 'ARMATURE':
            continue
        if other.get(RIG_ID_KEY) == rig_id:
            raise AnimationLinkError('This rig association was duplicated. Explicitly rebind the intended rig.')
        if LINK_KEY in other:
            saved = _json(other[LINK_KEY], 'Other saved Action association')
            other_id = saved.get('identity', {}).get('linkId', '')
            if other_id and uuid.UUID(other_id) == uuid.UUID(link_id):
                raise AnimationLinkError('This Link is already bound to another rig in the Blender file.')


def _active_slot(rig, action):
    ad = rig.animation_data
    if ad is None or ad.action is not action or ad.action_slot is None:
        raise AnimationLinkError('Select the bound Action and its Object slot on this rig first.')
    if ad.action_slot.target_id_type != 'OBJECT':
        raise AnimationLinkError('The linked Action requires an Object slot.')
    return ad.action_slot.handle


def bind_action(rig, action, manifest_path, export_rig_name, model_file):
    """Explicitly bind one imported Action; does not save the user's .blend."""
    if rig is None or rig.type != 'ARMATURE' or rig.library or action is None or action.library:
        raise AnimationLinkError('Bind a local character rig and a local imported Action.')
    path = Path(manifest_path).resolve()
    manifest = load_link(path)
    if (_absolute(action.get(PACKAGE_KEY), 'Action source packet') != _absolute(manifest['sourcePackage'], 'sourcePackage')
            or action.get(PACKAGE_HASH_KEY, '').lower() != manifest['sourcePackageSha256'].lower()):
        raise AnimationLinkError('This Action was not imported from the linked packet revision.')
    slot = _active_slot(rig, action)
    if (not isinstance(export_rig_name, str) or not export_rig_name.strip()
            or any(ord(char) < 32 or char in '/\\' for char in export_rig_name)):
        raise AnimationLinkError('The model rig root name is invalid.')
    model = _absolute(str(model_file), 'model_file') if model_file else None
    if model is not None and (not model.is_file() or model.suffix.lower() != '.fbx'):
        raise AnimationLinkError('Choose the FBX model used to import this Action.')
    model_hash = _sha256(model) if model else ''
    if manifest.get('modelFile') and model != _absolute(manifest['modelFile'], 'modelFile'):
        raise AnimationLinkError('The chosen model differs from the animation Link model.')
    if manifest.get('modelSha256') and model_hash != manifest['modelSha256'].lower():
        raise AnimationLinkError('The chosen model hash differs from the animation Link model.')
    rig_id = rig.get(RIG_ID_KEY) or uuid.uuid4().hex
    _check_duplicates(rig, rig_id, manifest['linkId'])
    record = {'version': 1, 'rig_id': rig_id, 'manifest_path': str(path),
              'identity': {key: manifest[key] for key in (*IDENTITY_FIELDS, *OPTIONAL_IDENTITY_FIELDS) if key in manifest},
              'action_slot': slot, 'action_name': action.name, 'export_rig_name': export_rig_name,
              'model_file': str(model) if model else '', 'model_sha256': model_hash}
    encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
    old = {key: rig[key] for key in (LINK_KEY, ACTION_KEY, RIG_ID_KEY) if key in rig}
    try:
        rig[RIG_ID_KEY], rig[ACTION_KEY], rig[LINK_KEY] = rig_id, action, encoded
    except Exception:
        for key in (LINK_KEY, ACTION_KEY, RIG_ID_KEY):
            if key in old:
                rig[key] = old[key]
            elif key in rig:
                del rig[key]
        raise


class _LinkLock:
    def __init__(self, manifest_path):
        self.path = Path(str(manifest_path) + '.blender.lock')
        self.token = uuid.uuid4().hex
        self.owned = False
        try:
            with self.path.open('x', encoding='ascii') as stream:
                self.owned = True
                stream.write(self.token)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise AnimationLinkError('This Link has an export lock. Finish its other export before retrying.') from exc
        except Exception:
            self.release()
            raise

    def release(self):
        if self.owned:
            self.owned = False
            if self.path.is_file() and self.path.read_text(encoding='ascii') == self.token:
                self.path.unlink()


def _publish_manifest(path, expected, updates):
    """The immutable pair is complete before this single publication point."""
    if set(updates) != OUTPUT_FIELDS:
        raise AnimationLinkError('The linked export has incomplete output metadata.')
    current, raw = _read_link(path)
    if _identity(current) != _identity(expected):
        raise AnimationLinkError('The Link source identity changed during export; the previous output is retained.')
    _verify_source(current)
    revision = current.get('revision', 0)
    if type(revision) is not int or not 0 <= revision < 2**31 - 1:
        raise AnimationLinkError('The Link revision must be a nonnegative integer below the signed 32-bit maximum.')
    merged = {**current, **updates, 'revision': revision + 1}
    temporary = Path(path).with_name('.' + uuid.uuid4().hex + '.animation-link.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(merged, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if Path(path).read_bytes() != raw:
            raise AnimationLinkError('The Link changed while publishing; retry after its other update finishes.')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return merged


def _revision_result(path, expected_identity, result, *, blend_file, source_action,
                     model_file='', model_sha256=''):
    """Shared finalization while the caller owns the per-Link lock."""
    path = Path(path).resolve()
    directory = path.parent / 'blender_revisions' / uuid.UUID(expected_identity['linkId']).hex
    fbx = _absolute(result.get('filepath'), 'Exported FBX')
    metadata = _absolute(result.get('metadata'), 'Export metadata')
    if not fbx.is_relative_to(directory.resolve()) or fbx.suffix.lower() != '.fbx' or metadata != fbx.with_suffix('.animation.json'):
        raise AnimationLinkError('The completed export does not belong to this Link revision directory.')
    if model_file:
        model = _absolute(str(model_file), 'model_file')
        if not model.is_file() or _sha256(model) != model_sha256:
            raise AnimationLinkError('The bound model changed during export; its Link was not updated.')
    fbx_hash = _sha256(fbx)
    if fbx_hash != result.get('fbx_sha256'):
        raise AnimationLinkError('The completed FBX changed before Link publication.')
    report = _json(metadata.read_bytes(), 'Export metadata')
    if (report.get('fbx_sha256') != fbx_hash
            or report.get('source_package_sha256', '').lower() != expected_identity['sourcePackageSha256'].lower()
            or _absolute(report.get('source_package'), 'Export source packet') !=
               _absolute(expected_identity['sourcePackage'], 'sourcePackage')):
        raise AnimationLinkError('The completed export metadata does not match the linked packet and FBX.')
    updates = {'blendFile': str(_absolute(blend_file, 'blendFile')) if blend_file else '',
               'sourceAction': source_action, 'fbxFile': str(fbx), 'fbxSha256': fbx_hash,
               'metadataFile': str(metadata), 'metadataSha256': _sha256(metadata)}
    published = _publish_manifest(path, expected_identity, updates)
    return {**result, 'link_manifest': str(path), 'link_id': expected_identity['linkId'],
            'revision': published['revision']}


def publish_linked_revision(manifest_path, expected_identity, result, *, blend_file, source_action,
                            model_file='', model_sha256=''):
    """Finalize a serially prepared worker result without Blender or another process.

    expected_identity is the captured manifest, or required identity fields plus
    any present OPTIONAL_IDENTITY_FIELDS (absence is itself protected).
    The result is animation_export._publish's completed immutable pair. Files
    must be under packet-folder/blender_revisions/<link UUID as 32 hex digits>.
    """
    _validate_identity(expected_identity)
    path = Path(manifest_path).resolve()
    lock = _LinkLock(path)
    try:
        return _revision_result(path, expected_identity, result, blend_file=blend_file,
            source_action=source_action, model_file=model_file, model_sha256=model_sha256)
    finally:
        lock.release()


def begin_linked_export(context, rig):
    """Export the bound active Action to a new revision, then advance its Link."""
    import bpy
    from . import animation_export

    saved = get_link(rig)
    if saved is None:
        raise AnimationLinkError('Import and bind a Unity animation Link first.')
    action = rig.get(ACTION_KEY)
    if action is None or _active_slot(rig, action) != saved['action_slot']:
        raise AnimationLinkError('The active Action slot differs from the saved Link.')
    _check_duplicates(rig, saved['rig_id'], saved['identity']['linkId'])
    path = Path(saved['manifest_path'])
    lock = _LinkLock(path)
    try:
        manifest = load_link(path)
        if _identity(manifest) != _identity(saved['identity']):
            raise AnimationLinkError('The Link now identifies another packet or clip. Import and bind it again.')
        if (action.get(PACKAGE_HASH_KEY, '').lower() != manifest['sourcePackageSha256'].lower()
                or _absolute(action.get(PACKAGE_KEY), 'Action source packet') !=
                   _absolute(manifest['sourcePackage'], 'sourcePackage')):
            raise AnimationLinkError('The Action source revision differs from the Link.')
        model = Path(saved['model_file']) if saved['model_file'] else None
        if model is not None and (not model.is_file() or _sha256(model) != saved['model_sha256']):
            raise AnimationLinkError('The bound model changed. Reimport the matching model and packet first.')
        start, end = map(float, action.frame_range)
        rate = action.get('unity_sample_rate', context.scene.render.fps / context.scene.render.fps_base)
        if isinstance(rate, bool) or not isinstance(rate, (float, int)) or not math.isfinite(rate) or not 1 <= rate <= 240:
            raise AnimationLinkError('The Action has an invalid sample rate.')
        directory = path.parent / 'blender_revisions' / uuid.UUID(manifest['linkId']).hex
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / ('action-' + uuid.uuid4().hex + '.fbx')
        source_action = action.name
        blend_file = str(Path(bpy.data.filepath).resolve()) if bpy.data.filepath else ''
        association = rig[LINK_KEY]

        def publish(job, result):
            if rig.get(LINK_KEY) != association or rig.get(ACTION_KEY) is not action:
                raise AnimationLinkError('The rig was rebound during export; its Link was not updated.')
            fbx, metadata = Path(result['filepath']).resolve(), Path(result['metadata']).resolve()
            if fbx != destination or metadata != destination.with_suffix('.animation.json'):
                raise AnimationLinkError('The completed export does not belong to this Link revision.')
            return _revision_result(path, manifest, result, blend_file=blend_file,
                source_action=source_action, model_file=saved['model_file'], model_sha256=saved['model_sha256'])

        job = animation_export.begin_export(context, rig, action, destination, frame_start=start, frame_end=end,
            loop=bool(action.get('unity_loop_time', False)), sample_rate=float(rate),
            export_rig_name=saved['export_rig_name'])
        job['_publish_callback'], job['_dispose_callback'] = publish, lock.release
        return job
    except Exception:
        lock.release()
        raise
