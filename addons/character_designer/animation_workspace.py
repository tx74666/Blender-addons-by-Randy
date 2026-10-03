"""Metadata-only character worklist shared with Unity Character Tuning.

Parsing and identity comparisons do not inspect the filesystem. Loading reads
only this bounded index; selected-item operations own Link/packet validation.
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
        identity = clip_identity(item)
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


def parse_workspace(raw):
    """Parse at most 1 MiB of UTF-8 JSON, retaining unknown metadata fields."""
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
    return _validate(record)


def load_workspace(path):
    """Read only the index, never a model, Link manifest, or animation packet."""
    try:
        path = Path(path).absolute()
        if path.name != MANIFEST_NAME:
            raise AnimationWorkspaceError('Choose ' + MANIFEST_NAME + '.')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        record = parse_workspace(raw)
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
