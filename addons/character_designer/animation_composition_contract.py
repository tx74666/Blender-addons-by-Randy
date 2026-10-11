"""Strict plain composition DTO; no Blender, filesystem, or application writes."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
import uuid

SCHEMA = 'cdesigner.animation-composition/1'
EXPORT_SCHEMA = 'cdesigner.animation-composition-export/1'
ROLE = 'UpperBodyWeapon'
SCOPE = 'UpperBodyOverride'
TIME_MODES = frozenset({'HoldPose', 'HoldLoop', 'Raise'})
BASE_ROLES = frozenset({'Spine', 'Chest', 'UpperChest', 'LeftShoulder', 'RightShoulder',
    'LeftUpperArm', 'RightUpperArm', 'LeftLowerArm', 'RightLowerArm', 'LeftHand', 'RightHand'})
FINGER_ROLES = frozenset(side + finger + joint for side in ('Left', 'Right')
    for finger in ('Thumb', 'Index', 'Middle', 'Ring', 'Little')
    for joint in ('Proximal', 'Intermediate', 'Distal'))
ALLOWED_ROLES = BASE_ROLES | FINGER_ROLES
FIELDS = frozenset({'schema', 'compositionId', 'applicationRole', 'baseClipGuid',
    'baseClipLocalId', 'baseClipSha256', 'referencePacketFile', 'referencePacketSha256',
    'editWorkItemId', 'editNewActionId', 'mask', 'blendMode', 'weight', 'humanRoles',
    'allowedBonePaths', 'editableControlIds', 'sourceRestSha256', 'timeMode',
    'referenceSpeed', 'editSpeed', 'referencePhase', 'editPhase', 'fps', 'sampleStart',
    'sampleEnd', 'holdWorkItemId', 'holdNewActionId', 'weaponGuid', 'weaponPrefabGuid',
    'weaponModelFile', 'weaponModelSha256', 'weaponSocketPath', 'weaponRigBonePath',
    'weaponLocalMatrix', 'weaponPrefabLocalMatrix', 'weaponContextFile', 'weaponContextSha256', 'gripTargets'})


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _text(value, label, *, empty=False):
    if (not isinstance(value, str) or len(value) > 2048 or
            (not empty and not value.strip()) or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid composition ' + label + '.')
    return value


def _hex(value, count, label, *, empty=False):
    if empty and value in (None, ''):
        return ''
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{' + str(count) + '}', value):
        raise ValueError('Invalid composition ' + label + '.')
    return value.lower()


def _uuid(value, label, *, empty=False):
    if empty and value in (None, ''):
        return ''
    if not isinstance(value, str):
        raise ValueError('Invalid composition ' + label + '.')
    try:
        parsed = uuid.UUID(value)
        if parsed.int == 0:
            raise ValueError('empty UUID')
        return str(parsed)
    except ValueError as error:
        raise ValueError('Invalid composition ' + label + '.') from error


def _number(value, label, low=None, high=None):
    if (type(value) not in (int, float) or not math.isfinite(value) or
            low is not None and value < low or high is not None and value > high):
        raise ValueError('Invalid composition ' + label + '.')
    return float(value)


def _path(value, label):
    _text(value, label)
    if not (PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()):
        raise ValueError('Composition ' + label + ' must be absolute.')
    return value


def _names(values, label, *, empty=False):
    if empty and values is None:
        return []
    if not isinstance(values, list) or (not empty and not values) or len(values) > 1024:
        raise ValueError('Composition ' + label + ' must be a bounded nonempty list.')
    values = [_text(v, label) for v in values]
    if len(values) != len(set(values)):
        raise ValueError('Composition ' + label + ' contains duplicate identities.')
    return values


def _matrix(values, label):
    if not isinstance(values, list) or len(values) != 16:
        raise ValueError('Composition ' + label + ' must contain 16 row-major values.')
    values = [_number(v, label) for v in values]
    if any(abs(values[i]) > 1e-8 for i in (12, 13, 14)) or abs(values[15] - 1) > 1e-8:
        raise ValueError('Composition ' + label + ' must be affine.')
    determinant = (values[0] * (values[5] * values[10] - values[6] * values[9])
        - values[1] * (values[4] * values[10] - values[6] * values[8])
        + values[2] * (values[4] * values[9] - values[5] * values[8]))
    if abs(determinant) < 1e-12:
        raise ValueError('Composition ' + label + ' must be nonsingular.')
    return values


def validate(spec, *, prepared=True):
    """Return an owned normalized DTO; unknown explicit composition rejects."""
    if not isinstance(spec, dict) or set(spec) != FIELDS or spec.get('schema') != SCHEMA:
        raise ValueError('Unsupported or incomplete composition contract.')
    result = deepcopy(spec)
    if (spec['applicationRole'] != ROLE or spec['mask'] != 'UpperBody' or
            spec['blendMode'] != 'Override' or type(spec['weight']) not in (int, float)
            or spec['weight'] != 1 or spec['timeMode'] not in TIME_MODES):
        raise ValueError('Direct composition editing requires UpperBody Override at weight 1.')
    for field in ('compositionId', 'editWorkItemId'):
        result[field] = _uuid(spec[field], field)
    for field in ('editNewActionId', 'holdWorkItemId', 'holdNewActionId'):
        result[field] = _uuid(spec[field], field, empty=True)
    if prepared and spec['timeMode'] == 'Raise' and not result['holdWorkItemId']:
        raise ValueError('Raise requires the actual following Hold work item identity.')
    if result['holdWorkItemId'] == result['editWorkItemId']:
        raise ValueError('Raise cannot follow itself as Hold.')
    if spec['timeMode'] != 'Raise' and (result['holdWorkItemId'] or result['holdNewActionId']):
        raise ValueError('Only Raise may reference a following Hold.')
    for field in ('baseClipGuid', 'weaponGuid', 'weaponPrefabGuid'):
        result[field] = _hex(spec[field], 32, field, empty=field != 'baseClipGuid')
    if type(spec['baseClipLocalId']) is not int or not -(2**63) <= spec['baseClipLocalId'] < 2**63 or spec['baseClipLocalId'] == 0:
        raise ValueError('Composition baseClipLocalId must be an exact nonzero int64.')
    for field in ('baseClipSha256', 'referencePacketSha256', 'sourceRestSha256'):
        result[field] = _hex(spec[field], 64, field, empty=not prepared and field != 'baseClipSha256')
    result['weaponModelFile'] = _path(spec['weaponModelFile'], 'weaponModelFile') if spec['weaponModelFile'] else ''
    result['weaponModelSha256'] = _hex(spec['weaponModelSha256'], 64, 'weaponModelSha256', empty=not result['weaponModelFile'])
    result['referencePacketFile'] = (_path(spec['referencePacketFile'], 'referencePacketFile')
        if prepared or spec['referencePacketFile'] else '')
    result['weaponContextFile'] = (_path(spec['weaponContextFile'], 'weaponContextFile')
        if prepared or spec['weaponContextFile'] else '')
    result['weaponContextSha256'] = _hex(spec['weaponContextSha256'], 64, 'weaponContextSha256', empty=not prepared)
    for field in ('weaponSocketPath', 'weaponRigBonePath'):
        result[field] = _text(spec[field] or '', field, empty=True)
    for field in ('humanRoles', 'allowedBonePaths', 'editableControlIds'):
        result[field] = _names(spec[field], field, empty=field == 'editableControlIds')
    if not set(result['humanRoles']) <= ALLOWED_ROLES:
        raise ValueError('Composition cannot own Root, Hips, legs, feet, head or unknown roles.')
    for field in ('referenceSpeed', 'editSpeed'):
        result[field] = _number(spec[field], field, 1e-5, 1000)
    for field in ('referencePhase', 'editPhase'):
        result[field] = _number(spec[field], field, 0, 1)
    result['fps'] = _number(spec['fps'], 'fps', 1, 240)
    for field in ('sampleStart', 'sampleEnd'):
        result[field] = _number(spec[field], field, -1048574, 1048574)
    if result['sampleEnd'] <= result['sampleStart']:
        raise ValueError('Composition requires a positive B duration, including Hold Pose.')
    if (result['sampleEnd'] - result['sampleStart']) * 240 > 20000:
        raise ValueError('Composition B duration exceeds the existing sampling bound.')
    result['weaponLocalMatrix'] = _matrix(spec['weaponLocalMatrix'], 'weaponLocalMatrix') if spec['weaponLocalMatrix'] is not None else None
    result['weaponPrefabLocalMatrix'] = (_matrix(spec['weaponPrefabLocalMatrix'], 'weaponPrefabLocalMatrix')
        if prepared or spec['weaponPrefabLocalMatrix'] is not None else None)
    grips = spec['gripTargets'] or []
    if not isinstance(grips, list) or len(grips) > 32:
        raise ValueError('Composition gripTargets must be a bounded list.')
    result['gripTargets'] = []
    for grip in grips:
        if not isinstance(grip, dict) or set(grip) != {'id', 'rigBonePath', 'localMatrix', 'controlId'}:
            raise ValueError('Composition grip target identity is incomplete.')
        result['gripTargets'].append({'id': _text(grip['id'], 'grip id'),
            'rigBonePath': _text(grip['rigBonePath'], 'grip bone path'),
            'controlId': _text(grip['controlId'] or '', 'grip control', empty=True),
            'localMatrix': _matrix(grip['localMatrix'], 'grip matrix')})
    if len({v['id'] for v in result['gripTargets']}) != len(grips):
        raise ValueError('Composition grip targets contain duplicate IDs.')
    if any(v['controlId'] and v['controlId'] not in result['editableControlIds']
           for v in result['gripTargets']):
        raise ValueError('A grip must reference an explicitly declared editable control.')
    return result


def identity(spec):
    """Includes reference/config revisions, independent of names and view mode."""
    return digest(validate(spec))


def validate_item(spec, item):
    spec = validate(spec)
    if _uuid(item.work_item_id, 'work item') != spec['editWorkItemId']:
        raise ValueError('Composition belongs to another editable B work item.')
    created = getattr(item, 'new_action_id', '') or ''
    if (_uuid(created, 'new Action', empty=True) != spec['editNewActionId']
            or bool(getattr(item, 'new_animation', False)) != bool(spec['editNewActionId'])):
        raise ValueError('Composition new Action identity differs from B.')
    return spec
