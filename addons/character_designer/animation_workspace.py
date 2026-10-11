"""Metadata-only character worklist shared with Unity Character Tuning.

Parsing and identity comparisons do not inspect the filesystem. Loading reads
only the bounded index and, when chosen, its explicit readable reference;
selected-item operations own Link/packet validation.
This module neither imports Blender nor owns client selections or edits.
"""

import json
import math
import ntpath
from pathlib import Path, PurePosixPath, PureWindowsPath
import posixpath
import re
import uuid


SCHEMA = 'randomrealm.animation-workspace/1'
MANIFEST_NAME = 'character_animation_workspace.json'
REFERENCE_SCHEMA = 'cdesigner.animation-workspace-reference/1'
REFERENCE_SUFFIX = '.workspace.json'
MAX_BYTES = 1024 * 1024
NOT_PREPARED = 'NOT_PREPARED'
LINK_AVAILABLE = 'LINK_AVAILABLE'


class AnimationWorkspaceError(ValueError):
    pass


def _text(value, label, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise AnimationWorkspaceError(label + ' must be a nonempty string.' if not optional
                                      else label + ' must be a string or null.')


def _hex(value, length, label):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{' + str(length) + '}', value):
        raise AnimationWorkspaceError(label + ' must contain ' + str(length) + ' hexadecimal characters.')
    return value.lower()


def _absolute_path(value, label):
    # Pure paths keep a saved UI snapshot safe to inspect, including on another
    # host. Never resolve symlinks, read a network share, or test existence here.
    if (not isinstance(value, str) or not value or any(ord(char) < 32 for char in value)
            or not (PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute())):
        raise AnimationWorkspaceError(label + ' must be an absolute path.')
    return value


def _path_identity(value):
    if PureWindowsPath(value).is_absolute():
        return ntpath.normcase(ntpath.normpath(value))
    return posixpath.normpath(value)


def clip_identity(item):
    """Return a stable slot key without rounding Unity's 64-bit local ID."""
    if not isinstance(item, dict):
        raise AnimationWorkspaceError('Each workspace clip must be a JSON object.')
    guid = _hex(item.get('clipGuid'), 32, 'clipGuid')
    local_id = item.get('clipLocalId')
    if type(local_id) is not int or local_id == 0 or not -(2**63) <= local_id < 2**63:
        raise AnimationWorkspaceError('clipLocalId must be an exact nonzero signed 64-bit integer.')
    return guid, local_id


def work_item_id(item):
    if not isinstance(item, dict):
        raise AnimationWorkspaceError('Each workspace clip must be a JSON object.')
    value = item.get('workItemId')
    if value is None or value == '':
        return ''
    try:
        if not isinstance(value, str):
            raise ValueError('not text')
        return uuid.UUID(value).hex
    except (ValueError, AttributeError, TypeError) as exc:
        raise AnimationWorkspaceError('workItemId must be a UUID or empty for a legacy row.') from exc


def base_clip_identity(item):
    """The destination slot is independent of the source animation."""
    guid, local_id = item.get('baseClipGuid'), item.get('baseClipLocalId')
    if guid in (None, '') and (local_id is None or type(local_id) is int and local_id == 0):
        return clip_identity(item)
    return clip_identity({'clipGuid': guid, 'clipLocalId': local_id})


def row_key(item):
    identity = work_item_id(item)
    guid, local_id = clip_identity(item)
    return 'work:' + identity if identity else guid + ':' + str(local_id)


def route_identity(item):
    """Frozen row/source/slot/scope provenance, excluding revision and names."""
    source_clip = clip_identity(item)
    source_character = item.get('sourceCharacterGuid') or ''
    if source_character:
        source_character = _hex(source_character, 32, 'sourceCharacterGuid')
    elif item.get('sourceCharacterGuid') not in (None, ''):
        raise AnimationWorkspaceError('sourceCharacterGuid must be a GUID or empty.')
    scope = []
    for field in ('purpose', 'editScope'):
        value = item.get(field)
        _text(value, field, optional=True)
        scope.append(value or '')
    source_only = item.get('sourceOnly', False)
    if type(source_only) is not bool:
        raise AnimationWorkspaceError('sourceOnly must be a boolean when supplied.')
    composition = item.get('composition')
    reference_source = item.get('compositionReferenceSource', False)
    if type(reference_source) is not bool:
        raise AnimationWorkspaceError('compositionReferenceSource must be a boolean when supplied.')
    reference_route = ()
    if reference_source:
        if composition is not None or scope[1] not in ('', 'All') or not work_item_id(item):
            raise AnimationWorkspaceError('A read-only composition reference requires All scope, its actual Work Item ID and no composition.')
        owner = _hex(item.get('compositionOwnerLinkGuid'), 32, 'compositionOwnerLinkGuid')
        reference_route = (('composition-reference-source', owner),)
    elif item.get('compositionOwnerLinkGuid') not in (None, ''):
        raise AnimationWorkspaceError('A composition reference owner requires its explicit read-only reference flag.')
    if source_only:
        local_id = item.get('baseClipLocalId')
        if (composition is not None or scope[1] not in ('', 'All')
                or not work_item_id(item)
                or item.get('baseClipGuid') not in (None, '')
                or not (local_id is None or type(local_id) is int and local_id == 0)):
            raise AnimationWorkspaceError('A prepared source-only reference requires All scope, no composition and no invented Base Clip slot.')
        return (work_item_id(item), source_clip, ('prepared-source-only', source_clip), source_character, *scope, *reference_route)
    if composition is not None:
        from . import animation_composition_contract as contract
        prepared = bool(item.get('sourcePackage') or item.get('linkManifest') and not item.get('needsRebuild'))
        spec = contract.validate(composition, prepared=prepared)
        if scope[1] != contract.SCOPE or work_item_id(item) != uuid.UUID(spec['editWorkItemId']).hex:
            raise AnimationWorkspaceError('Composition belongs to another B work item or scope.')
        return (work_item_id(item), source_clip, ('armed-composition', spec['compositionId']),
                source_character, *scope, contract.digest(spec))
    if scope[1] == 'UpperBodyOverride':
        raise AnimationWorkspaceError('UpperBodyOverride requires its explicit composition record.')
    return (work_item_id(item), source_clip, base_clip_identity(item), source_character, *scope, *reference_route)


def prepare_request(record):
    """None means a legacy index; an explicit empty request adds nothing."""
    value = record.get('prepareRequestId')
    values = record.get('preparedWorkItemIds')
    has_work_items = any(work_item_id(item) for item in record['clips'])
    if value in (None, '') and values in (None, []) and not has_work_items:
        return None  # JsonUtility may emit null default fields in legacy /1.
    if value in (None, '') and values in (None, []):
        return '', ()  # New rows require an explicit preparation request.
    if not isinstance(values, list):
        raise AnimationWorkspaceError('preparedWorkItemIds must be an array.')
    request = work_item_id({'workItemId': value})
    ids = tuple(work_item_id({'workItemId': value}) for value in values)
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise AnimationWorkspaceError('preparedWorkItemIds must contain unique, nonempty UUIDs.')
    if ids and not request:
        raise AnimationWorkspaceError('A nonempty preparation request needs a prepareRequestId.')
    known = {work_item_id(item) for item in record['clips']}
    if not set(ids) <= known:
        raise AnimationWorkspaceError('The preparation request contains an unknown workItemId.')
    return request, ids


def workspace_identity(record):
    """Identify the target/model snapshot, excluding names and clip selections.

    Callers must reject a changed identity when refreshing an editing workspace;
    a matching workspace UUID alone does not prove the model is unchanged.
    """
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise AnimationWorkspaceError('Unsupported animation workspace schema.')
    try:
        value = record['workspaceId']
        if not isinstance(value, str):
            raise ValueError('workspaceId is not a string')
        workspace_id = uuid.UUID(value).hex
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise AnimationWorkspaceError('The animation workspace has no valid workspaceId.') from exc
    target_guid = _hex(record.get('targetGuid'), 32, 'targetGuid')
    _text(record.get('targetName'), 'targetName')
    model = _absolute_path(record.get('modelFile'), 'modelFile')
    model_hash = _hex(record.get('modelSha256'), 64, 'modelSha256')
    return SCHEMA, workspace_id, target_guid, _path_identity(model), model_hash


def _validate(record):
    workspace_identity(record)
    clips = record.get('clips')
    if not isinstance(clips, list):
        raise AnimationWorkspaceError('The animation workspace clips field must be an array.')
    identities = set()
    manifests = set()
    for item in clips:
        route_identity(item)
        identity = row_key(item)
        if identity in identities:
            raise AnimationWorkspaceError('The animation workspace contains a duplicate clip identity.')
        identities.add(identity)
        _text(item.get('clipName'), 'clipName')
        for field in ('sourceName', 'sourceAsset'):
            if field in item:
                _text(item[field], field, optional=True)
        manifest = item.get('linkManifest')
        if manifest is None or manifest == '':
            continue
        _absolute_path(manifest, 'linkManifest')
        # One Link represents one clip. Sharing it across different index slots
        # would make the selected row ambiguous even before opening a packet.
        key = _path_identity(manifest)
        if key in manifests:
            raise AnimationWorkspaceError('Different workspace clips cannot share one linkManifest.')
        manifests.add(key)
    prepare_request(record)
    return record


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AnimationWorkspaceError('The animation workspace contains duplicate JSON keys: ' + key)
        result[key] = value
    return result


def _constant(value):
    raise AnimationWorkspaceError('The animation workspace contains a non-finite JSON number: ' + value)


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        _constant(value)
    return result


def _decode_json(raw):
    """Decode bounded UTF-8 JSON with the same strictness for both file types."""
    try:
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        elif isinstance(raw, bytearray):
            raw = bytes(raw)
        if not isinstance(raw, bytes):
            raise AnimationWorkspaceError('The animation workspace must be UTF-8 JSON text.')
        if len(raw) > MAX_BYTES:
            raise AnimationWorkspaceError('The animation workspace exceeds the 1 MiB limit.')
        # Decode explicitly: json.loads(bytes) also accepts UTF-16/32, which are
        # outside this contract. A UTF-8 BOM from a Unity writer is harmless.
        record = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_object,
                            parse_constant=_constant, parse_float=_float)
    except AnimationWorkspaceError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise AnimationWorkspaceError('The animation workspace is not valid UTF-8 JSON.') from exc
    return record


def parse_workspace(raw):
    """Parse at most 1 MiB of UTF-8 JSON, retaining unknown metadata fields."""
    return _validate(_decode_json(raw))


def _uuid(value, label):
    try:
        if not isinstance(value, str):
            raise ValueError('not text')
        return uuid.UUID(value).hex
    except (ValueError, AttributeError, TypeError) as exc:
        raise AnimationWorkspaceError(label + ' must be a valid UUID.') from exc


def _parse_reference(raw):
    record = _decode_json(raw)
    if not isinstance(record, dict) or record.get('schema') != REFERENCE_SCHEMA:
        raise AnimationWorkspaceError('Unsupported workspace entry schema.')
    _uuid(record.get('workspaceId'), 'Entry workspaceId')
    _uuid(record.get('projectId'), 'Entry projectId')
    _hex(record.get('targetGuid'), 32, 'Entry targetGuid')
    _text(record.get('targetName'), 'Entry targetName')
    source = _absolute_path(record.get('workspaceManifest'), 'Entry workspaceManifest')
    if Path(source).name != MANIFEST_NAME:
        raise AnimationWorkspaceError('The workspace entry must point directly to ' + MANIFEST_NAME + '; reference chains are not supported.')
    return record


def _read_bytes(path):
    with path.open('rb') as stream:
        return stream.read(MAX_BYTES + 1)


def _load_reference(path):
    try:
        reference = _parse_reference(_read_bytes(path))
    except (AnimationWorkspaceError, OSError, TypeError, ValueError) as exc:
        raise AnimationWorkspaceError('The workspace entry could not be read: ' + str(exc)) from exc
    source = Path(reference['workspaceManifest']).absolute()
    try:
        # Always parse an actual index, never call load_workspace recursively.
        record = parse_workspace(_read_bytes(source))
    except (AnimationWorkspaceError, OSError, TypeError, ValueError) as exc:
        raise AnimationWorkspaceError('The workspace entry source is unavailable: ' + str(exc)) from exc
    try:
        matches = (_uuid(reference['workspaceId'], 'Entry workspaceId') ==
                   _uuid(record.get('workspaceId'), 'Source workspaceId') and
                   _uuid(reference['projectId'], 'Entry projectId') ==
                   _uuid(record.get('projectId'), 'Source projectId') and
                   _hex(reference['targetGuid'], 32, 'Entry targetGuid') ==
                   _hex(record.get('targetGuid'), 32, 'Source targetGuid'))
    except AnimationWorkspaceError as exc:
        raise AnimationWorkspaceError('The workspace entry source identity changed: ' + str(exc)) from exc
    if not matches:
        raise AnimationWorkspaceError('The workspace entry source identity changed. Choose the matching workspace entry.')
    # Labels and entry paths never replace the live catalog or its real path.
    return {**record, '_manifest_path': str(source)}


def load_workspace(path):
    """Read an index or explicit reference, never models, Links or packets."""
    try:
        path = Path(path).absolute()
        if path.name != MANIFEST_NAME:
            if path.name.lower().endswith(REFERENCE_SUFFIX):
                return _load_reference(path)
            raise AnimationWorkspaceError('Choose ' + MANIFEST_NAME + ' or a readable ' + REFERENCE_SUFFIX + ' entry.')
        record = parse_workspace(_read_bytes(path))
    except AnimationWorkspaceError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise AnimationWorkspaceError('The animation workspace could not be read: ' + str(exc)) from exc
    return {**record, '_manifest_path': str(path)}


def probe_link_status(item):
    """Explicit existence probe, not for UI draw and not Link validation.

    A missing path leaves the row visible as NOT_PREPARED. LINK_AVAILABLE means
    only that a manifest file exists; selected-item code must validate its Link,
    target/clip/model identity and source packet before importing an Action.
    """
    clip_identity(item)
    manifest = item.get('linkManifest')
    if manifest is None or manifest == '':
        return NOT_PREPARED
    _absolute_path(manifest, 'linkManifest')
    try:
        return LINK_AVAILABLE if Path(manifest).is_file() else NOT_PREPARED
    except OSError:
        return NOT_PREPARED
