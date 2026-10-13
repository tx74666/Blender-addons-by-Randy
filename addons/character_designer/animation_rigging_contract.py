"""Exact two-arm source controls DTO; no Blender, filesystem or application I/O.

Parent matrices and weights are capture-only context. A return contains only
the existing Right/Left Target position/rotation and Hint position channels.
The full packet SHA and native Action identities come from the caller.
"""
from copy import deepcopy
from pathlib import PurePosixPath, PureWindowsPath
import hashlib
import json
import math
import re
import struct
import uuid

SCHEMA = 'cdesigner.two-bone-rigging/1'
SIDES = ('Right', 'Left')
MAX_FRAMES = 20001
CLIP_FIELDS = frozenset({'sourceClipPath', 'sourceClipGuid', 'sourceClipLocalId', 'sourceClipSha256'})
APPLY_IDENTITY_FIELDS = frozenset({'targetLayoutSha256', 'baseControllerGuid',
    'baseControllerDependencyHash', 'avatarDependencyHash'})
OPTIONAL_CAPTURE_FIELDS = APPLY_IDENTITY_FIELDS | {'weaponPreview'}
CAPTURE_FIELDS = frozenset({'schema', 'targetGuid', 'targetDependencyHash', 'sourceMode',
    'bindingsSha256', 'duration', 'sampleRate', 'loopTime', 'sourceTimeMode',
    'newActionId', 'previewEnabled', 'bindings', 'frames'}) | CLIP_FIELDS | OPTIONAL_CAPTURE_FIELDS
WEAPON_PREVIEW_FIELDS = frozenset({'weaponContextFile', 'weaponContextSha256', 'weaponGuid',
    'weaponPrefabGuid', 'weaponModelFile', 'weaponModelSha256', 'weaponSocketPath',
    'weaponRigBonePath', 'weaponLocalMatrix', 'weaponPrefabLocalMatrix'})
BINDING_FIELDS = frozenset({'rigPath', 'rigWeight', 'constraintPath',
    'rootPath', 'midPath', 'tipPath', 'targetPath', 'targetParentPath', 'hintPath',
    'hintParentPath', 'constraintWeight', 'targetPositionWeight', 'targetRotationWeight',
    'hintWeight', 'maintainTargetPositionOffset', 'maintainTargetRotationOffset'})
LOCAL_FIELDS = frozenset({'targetPosition', 'targetRotation', 'hintPosition'})
PARENT_FIELDS = frozenset({'targetParentRoot', 'hintParentRoot'})
EXPORT_FIELDS = frozenset({'schema', 'sourcePacketSha256', 'bindingsSha256', 'targetGuid',
    'workItemId', 'newActionId', 'sourceAction', 'sourceActionSlot', 'targetInputAction',
    'targetInputActionSlot', 'duration', 'sampleRate', 'loopTime', 'frames'})
BINDING_PATH_ORDER = ('rigPath', 'constraintPath', 'rootPath', 'midPath', 'tipPath',
    'targetPath', 'targetParentPath', 'hintPath', 'hintParentPath')
BINDING_WEIGHT_ORDER = ('rigWeight', 'constraintWeight', 'targetPositionWeight',
    'targetRotationWeight', 'hintWeight')


def _shape(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Unsupported or incomplete rigging ' + label + '.')


def _text(value, label, *, empty=False):
    if (not isinstance(value, str) or len(value) > 2048 or value != value.strip()
            or not empty and not value or any(ord(char) < 32 for char in value)):
        raise ValueError('Invalid rigging ' + label + '.')
    return value


def _transform_path(value, label, *, empty=False):
    value = _text(value, label, empty=empty)
    if value and ('\\' in value or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Rigging ' + label + ' must be an exact relative source path.')
    return value


def _asset_path(value, label):
    value = _transform_path(value, label)
    if ':' in value:
        raise ValueError('Rigging ' + label + ' must be an exact relative source path.')
    return value


def _absolute_file(value, label):
    value = _text(value, label)
    if (not (PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute())
            or value.endswith(('/', '\\')) or any(part in ('.', '..') for part in re.split(r'[/\\]', value))):
        raise ValueError('Rigging ' + label + ' must retain an absolute context file path.')
    return value


def _hex(value, count, label, *, nonzero=False):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{' + str(count) + '}', value):
        raise ValueError('Invalid rigging ' + label + '.')
    if nonzero and not int(value, 16):
        raise ValueError('Rigging ' + label + ' cannot be empty.')
    return value.lower()


def _number(value, label, *, low=None, high=None):
    if type(value) not in (int, float):
        raise ValueError('Invalid rigging ' + label + '.')
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError('Invalid rigging ' + label + '.') from error
    if (not math.isfinite(number) or low is not None and number < low
            or high is not None and number > high):
        raise ValueError('Invalid rigging ' + label + '.')
    return number


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError('Rigging ' + label + ' must be a boolean.')
    return value


def _int64(value, label, *, positive=False, nonzero=False):
    if (type(value) is not int or not -(2**63) <= value < 2**63
            or positive and value <= 0 or nonzero and value == 0):
        raise ValueError('Rigging ' + label + ' must be an exact int64.')
    return value


def _slot(value, label):
    if type(value) is not int or not 0 <= value < 2**31:
        raise ValueError('Rigging ' + label + ' must be a nonnegative exact int32.')
    return value


def _uuid(value, label):
    _text(value, label)
    try:
        parsed = uuid.UUID(value)
        if not parsed.int:
            raise ValueError('empty UUID')
    except ValueError as error:
        raise ValueError('Invalid rigging ' + label + '.') from error
    return parsed.hex


def _uuid_n(value, label):
    return _hex(value, 32, label, nonzero=True)


def _vector(value, count, label):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError('Rigging ' + label + ' has the wrong component count.')
    return [_number(component, label) for component in value]


def _rotation(value):
    values = _vector(value, 4, 'targetRotation xyzw')
    length_squared = sum(component * component for component in values)
    if not math.isfinite(length_squared) or abs(length_squared - 1) > 1e-4:
        raise ValueError('Rigging Target rotation must be a finite unit xyzw quaternion.')
    return values


def _matrix(value, label):
    values = _vector(value, 16, label)
    if any(abs(values[index]) > 1e-6 for index in (12, 13, 14)) or abs(values[15] - 1) > 1e-6:
        raise ValueError('Rigging ' + label + ' must be an affine row-major matrix.')
    determinant = (values[0] * (values[5] * values[10] - values[6] * values[9])
        - values[1] * (values[4] * values[10] - values[6] * values[8])
        + values[2] * (values[4] * values[9] - values[5] * values[8]))
    if not math.isfinite(determinant) or abs(determinant) < 1e-12:
        raise ValueError('Rigging ' + label + ' must be invertible.')
    return values


def _weapon_preview(value):
    _shape(value, WEAPON_PREVIEW_FIELDS, 'weapon preview context')
    result = deepcopy(value)
    for name in ('weaponContextFile', 'weaponModelFile'):
        result[name] = _absolute_file(value[name], name)
    for name in ('weaponContextSha256', 'weaponModelSha256'):
        result[name] = _hex(value[name], 64, name)
    for name in ('weaponGuid', 'weaponPrefabGuid'):
        result[name] = _hex(value[name], 32, name, nonzero=True)
    for name in ('weaponSocketPath', 'weaponRigBonePath'):
        result[name] = _transform_path(value[name], name)
    if _parent(result['weaponSocketPath']) != result['weaponRigBonePath']:
        raise ValueError('The display weapon socket must be a direct child of its captured hand mount.')
    for name in ('weaponLocalMatrix', 'weaponPrefabLocalMatrix'):
        result[name] = _matrix(value[name], name)
    return result


def _parent(path):
    return path.rsplit('/', 1)[0] if '/' in path else ''


def _clock(value):
    duration = _number(value['duration'], 'duration', low=0)
    if duration <= 0:
        raise ValueError('Rigging duration must be positive.')
    rate = _number(value['sampleRate'], 'sampleRate', low=1, high=240)
    loop = _boolean(value['loopTime'], 'loopTime')
    if not math.isfinite(duration * rate) or duration * rate > MAX_FRAMES:
        raise ValueError('Rigging sampling exceeds the existing 20001-frame bound.')
    # The existing Unity packet sampler uses float division and a 1e-6f end
    # exclusion. Mirror that one clock, including its serialized float edges.
    single = lambda number: struct.unpack('<f', struct.pack('<f', number))[0]
    end = single(single(duration) - single(1e-6))
    times = []
    for index in range(MAX_FRAMES):
        time = single(index / single(rate))
        if time >= end:
            return duration, rate, loop, times + [duration]
        times.append(time)
    raise ValueError('Rigging sampling exceeds the existing 20001-frame bound.')


def _controls(value, *, capture):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('Rigging controls must follow the two binding indices Right, Left.')
    result = []
    for control in value:
        _shape(control, LOCAL_FIELDS | PARENT_FIELDS if capture else LOCAL_FIELDS, 'control')
        row = {'targetPosition': _vector(control['targetPosition'], 3, 'targetPosition'),
            'targetRotation': _rotation(control['targetRotation']),
            'hintPosition': _vector(control['hintPosition'], 3, 'hintPosition')}
        if capture:
            for name in PARENT_FIELDS:
                row[name] = _matrix(control[name], name)
        result.append(row)
    return result


def _frames(value, times, *, capture):
    if not isinstance(value, list) or len(value) != len(times) or not 1 <= len(value) <= MAX_FRAMES:
        raise ValueError('Rigging frames must retain the exact bounded source clock.')
    result = []
    for index, frame in enumerate(value):
        _shape(frame, {'time', 'controls'}, 'frame')
        time = _number(frame['time'], 'frame time', low=0)
        if (abs(time - times[index]) > 1e-6 if capture else time != times[index]):
            raise ValueError('Rigging frame time differs from its source clock.')
        if index and time <= result[-1]['time']:
            raise ValueError('Rigging frame times must increase strictly.')
        result.append({'time': time, 'controls': _controls(frame['controls'], capture=capture)})
    if result[-1]['time'] != times[-1]:
        raise ValueError('Rigging final frame must equal the exact source duration.')
    return result


def _bindings(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError('Rigging requires the exact Right and Left arm bindings.')
    result, bones, inputs, constraints, rig_weights = [], set(), set(), set(), {}
    for binding in value:
        _shape(binding, BINDING_FIELDS, 'binding')
        row = deepcopy(binding)
        for name in ('rigPath', 'constraintPath', 'rootPath', 'midPath', 'tipPath', 'targetPath', 'hintPath'):
            row[name] = _transform_path(binding[name], name)
        for name in ('targetParentPath', 'hintParentPath'):
            row[name] = _transform_path(binding[name], name, empty=True)
        if (not row['constraintPath'].startswith(row['rigPath'] + '/')
                or _parent(row['midPath']) != row['rootPath'] or _parent(row['tipPath']) != row['midPath']
                or _parent(row['targetPath']) != row['targetParentPath']
                or _parent(row['hintPath']) != row['hintParentPath']):
            raise ValueError('Rigging source chain or control parent hierarchy differs.')
        chain = {row[name] for name in ('rootPath', 'midPath', 'tipPath')}
        controls = {row['targetPath'], row['hintPath']}
        if (len(chain) != 3 or len(controls) != 2 or bones & chain or inputs & controls
                or row['constraintPath'] in constraints):
            raise ValueError('Rigging source paths must identify unique chains and controls.')
        for name in ('rigWeight', 'constraintWeight', 'targetPositionWeight', 'targetRotationWeight', 'hintWeight'):
            row[name] = _number(binding[name], name, low=0, high=1)
        for name in ('maintainTargetPositionOffset', 'maintainTargetRotationOffset'):
            row[name] = _boolean(binding[name], name)
        if row['rigPath'] in rig_weights and rig_weights[row['rigPath']] != row['rigWeight']:
            raise ValueError('One original Rig cannot have contradictory captured weights.')
        rig_weights[row['rigPath']] = row['rigWeight']
        bones.update(chain)
        inputs.update(controls)
        constraints.add(row['constraintPath'])
        result.append(row)
    if inputs & (bones | constraints | set(rig_weights)):
        raise ValueError('Rigging Target/Hint paths cannot alias bones, constraints or Rig owners.')
    return result


def bindings_digest(value):
    """Unity's published ordered binding fingerprint, with float32 wire weights.

    The producer authenticates the Avatar's Right/Left order. Preserve those
    two indices verbatim; no side aliases or name-based arm guessing are used.
    """
    data = bytearray()
    for binding in _bindings(value):
        for name in BINDING_PATH_ORDER:
            try:
                encoded = binding[name].encode('utf-8')
            except UnicodeEncodeError as error:
                raise ValueError('Rigging source paths must be valid UTF-8.') from error
            data.extend(struct.pack('<i', len(encoded)))
            data.extend(encoded)
        for name in BINDING_WEIGHT_ORDER:
            data.extend(struct.pack('<f', binding[name]))
        data.extend(bytes((int(binding['maintainTargetPositionOffset']),
            int(binding['maintainTargetRotationOffset']))))
    return hashlib.sha256(data).hexdigest()


def validate_capture(value, *, packet=None):
    """Own a strict capture; optional already-verified packet binds target/clock/chain.

    sourceClip* describes the independent Target input clip, not the packet's
    base clip. prefab-default alone permits omitted/empty sourceClip* fields.
    The published binding fingerprint seals their fixed Right/Left indices.
    """
    if (not isinstance(value, dict)
            or not (CAPTURE_FIELDS - CLIP_FIELDS - OPTIONAL_CAPTURE_FIELDS) <= set(value)
            or not set(value) <= CAPTURE_FIELDS or value.get('schema') != SCHEMA
            or value.get('sourceMode') not in ('clip', 'prefab-default')
            or value.get('sourceTimeMode') != 'normalized-base-phase'):
        raise ValueError('Unsupported or incomplete two-bone rigging capture.')
    result = deepcopy(value)
    result['targetGuid'] = _hex(value['targetGuid'], 32, 'targetGuid', nonzero=True)
    result['targetDependencyHash'] = _hex(value['targetDependencyHash'], 32, 'targetDependencyHash')
    result['bindingsSha256'] = _hex(value['bindingsSha256'], 64, 'bindingsSha256')
    result['newActionId'] = _uuid_n(value['newActionId'], 'newActionId')
    result['previewEnabled'] = _boolean(value['previewEnabled'], 'previewEnabled')
    apply_fields = set(value) & APPLY_IDENTITY_FIELDS
    if apply_fields and apply_fields != APPLY_IDENTITY_FIELDS:
        raise ValueError('Rigging Apply identity fields must occur as one complete group.')
    if apply_fields:
        result['targetLayoutSha256'] = _hex(value['targetLayoutSha256'], 64, 'targetLayoutSha256')
        result['baseControllerGuid'] = _hex(value['baseControllerGuid'], 32, 'baseControllerGuid', nonzero=True)
        for name in ('baseControllerDependencyHash', 'avatarDependencyHash'):
            result[name] = _hex(value[name], 32, name)
    if value.get('weaponPreview') is not None:
        result['weaponPreview'] = _weapon_preview(value['weaponPreview'])
    if value['sourceMode'] == 'clip':
        if not CLIP_FIELDS <= set(value):
            raise ValueError('An explicit rigging source clip needs its complete identity.')
        result['sourceClipPath'] = _asset_path(value['sourceClipPath'], 'sourceClipPath')
        result['sourceClipGuid'] = _hex(value['sourceClipGuid'], 32, 'sourceClipGuid', nonzero=True)
        result['sourceClipLocalId'] = _int64(value['sourceClipLocalId'], 'sourceClipLocalId', nonzero=True)
        result['sourceClipSha256'] = _hex(value['sourceClipSha256'], 64, 'sourceClipSha256')
    else:
        for name in ('sourceClipPath', 'sourceClipGuid', 'sourceClipSha256'):
            if value.get(name) not in (None, ''):
                raise ValueError('prefab-default cannot claim a source clip identity.')
            result[name] = ''
        if type(value.get('sourceClipLocalId', 0)) is not int or value.get('sourceClipLocalId', 0) != 0:
            raise ValueError('prefab-default sourceClipLocalId must be zero.')
        result['sourceClipLocalId'] = 0
    duration, rate, loop, times = _clock(value)
    result.update(duration=duration, sampleRate=rate, loopTime=loop,
        bindings=_bindings(value['bindings']), frames=_frames(value['frames'], times, capture=True))
    if (result.get('weaponPreview') is not None
            and result['weaponPreview']['weaponRigBonePath'] != result['bindings'][0]['tipPath']):
        raise ValueError('The display weapon must retain the exact authenticated Right hand mount.')
    if bindings_digest(result['bindings']) != result['bindingsSha256']:
        raise ValueError('Rigging bindings differ from their published source fingerprint.')
    if packet is not None:
        if not isinstance(packet, dict):
            raise ValueError('Rigging admission needs an already-verified packet DTO.')
        packet_clock = (_number(packet.get('duration'), 'packet duration', low=0),
            _number(packet.get('sampleRate'), 'packet sampleRate', low=1, high=240),
            _boolean(packet.get('loopTime'), 'packet loopTime'))
        if (_hex(packet.get('targetGuid'), 32, 'packet targetGuid', nonzero=True) != result['targetGuid']
                or packet_clock != (duration, rate, loop)
                or not isinstance(packet.get('frames'), list)
                or [_number(frame.get('time') if isinstance(frame, dict) else None,
                            'packet frame time', low=0) for frame in packet['frames']]
                    != [frame['time'] for frame in result['frames']]):
            raise ValueError('Rigging target or sampled clock differs from the source packet.')
        rows = packet.get('bones')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Rigging admission needs the packet exact bone hierarchy.')
        by_path = {_transform_path(row.get('path'), 'packet bone path'): index for index, row in enumerate(rows)}
        if len(by_path) != len(rows):
            raise ValueError('The packet bone paths are ambiguous.')
        if result.get('weaponPreview') is not None:
            if result['weaponPreview']['weaponRigBonePath'] not in by_path:
                raise ValueError('The display weapon mount is absent from the exact packet bones.')
        for binding in result['bindings']:
            paths = [binding[name] for name in ('rootPath', 'midPath', 'tipPath')]
            if any(path not in by_path for path in paths):
                raise ValueError('Rigging arm chain is absent from the exact packet bones.')
            for parent, child in zip(paths, paths[1:]):
                index = rows[by_path[child]].get('parent')
                if type(index) is not int or index != by_path[parent]:
                    raise ValueError('Rigging arm chain differs from the exact packet parent indices.')
    return result


def capture_digest(value):
    """Fingerprint this normalized capture only; not Unity bindings or packet SHA."""
    data = json.dumps(validate_capture(value), sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def _return_identity(source_packet_sha256, work_item_id, new_action_id, source_action,
                     source_action_slot, target_input_action, target_input_action_slot):
    result = {'sourcePacketSha256': _hex(source_packet_sha256, 64, 'sourcePacketSha256'),
        'workItemId': _uuid(work_item_id, 'workItemId'), 'newActionId': _uuid_n(new_action_id, 'newActionId'),
        'sourceAction': _text(source_action, 'sourceAction'),
        'sourceActionSlot': _slot(source_action_slot, 'sourceActionSlot'),
        'targetInputAction': _text(target_input_action, 'targetInputAction'),
        'targetInputActionSlot': _slot(target_input_action_slot, 'targetInputActionSlot')}
    if (uuid.UUID(result['workItemId']).hex == uuid.UUID(result['newActionId']).hex
            or result['sourceAction'] == result['targetInputAction']):
        raise ValueError('Rigging return needs a separate draft and independent Target input Action.')
    return result


def validate_export(value, capture, *, source_packet_sha256, work_item_id, new_action_id,
                    source_action, source_action_slot, target_input_action, target_input_action_slot):
    """Validate only authorized source-local controls against actual caller identities."""
    capture = validate_capture(capture)
    _shape(value, EXPORT_FIELDS, 'return')
    expected = _return_identity(source_packet_sha256, work_item_id, new_action_id,
        source_action, source_action_slot, target_input_action, target_input_action_slot)
    actual = _return_identity(value['sourcePacketSha256'], value['workItemId'], value['newActionId'],
        value['sourceAction'], value['sourceActionSlot'], value['targetInputAction'], value['targetInputActionSlot'])
    if (value['schema'] != SCHEMA or actual != expected
            or actual['newActionId'] != capture['newActionId']
            or _hex(value['bindingsSha256'], 64, 'bindingsSha256') != capture['bindingsSha256']
            or _hex(value['targetGuid'], 32, 'targetGuid', nonzero=True) != capture['targetGuid']
            or any(value[name] != capture[name] for name in ('duration', 'sampleRate', 'loopTime'))
            or type(value['loopTime']) is not bool):
        raise ValueError('Rigging return identity, binding context or clock changed.')
    _number(value['duration'], 'duration', low=0)
    _number(value['sampleRate'], 'sampleRate', low=1, high=240)
    result = deepcopy(value)
    result.update(actual, bindingsSha256=capture['bindingsSha256'], targetGuid=capture['targetGuid'])
    result['frames'] = _frames(value['frames'], [frame['time'] for frame in capture['frames']], capture=False)
    return result


def build_export(capture, samples, *, source_packet_sha256, work_item_id, new_action_id,
                 source_action, source_action_slot, target_input_action, target_input_action_slot):
    """Build a control-only return, binding the external full packet and both Actions."""
    capture = validate_capture(capture)
    identity = _return_identity(source_packet_sha256, work_item_id, new_action_id,
        source_action, source_action_slot, target_input_action, target_input_action_slot)
    result = {'schema': SCHEMA, **identity, 'bindingsSha256': capture['bindingsSha256'],
        'targetGuid': capture['targetGuid'], 'duration': capture['duration'],
        'sampleRate': capture['sampleRate'], 'loopTime': capture['loopTime'], 'frames': deepcopy(samples)}
    return validate_export(result, capture, source_packet_sha256=source_packet_sha256,
        work_item_id=work_item_id, new_action_id=new_action_id, source_action=source_action,
        source_action_slot=source_action_slot, target_input_action=target_input_action,
        target_input_action_slot=target_input_action_slot)
