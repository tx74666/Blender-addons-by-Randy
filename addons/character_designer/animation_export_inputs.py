"""Explicit, retained inputs for sparse single-Action exports.

Capture is the only authoring operation. Export validates the retained record
and applies it only to the disposable worker rig, never to the artist rig.
"""
import hashlib
import json
import math

RECORD = 'character_designer_animation_export_base_v1'
COUNT = RECORD + '_count'
SCHEMA = 'cdesigner.animation-export-base/1'
JOB_KEY = 'export_base_pose'
MAX_RECORD = 4 * 1024 * 1024
MAX_BONES = 1024
MAX_CURVES = 8192
MAX_INPUTS = 50000
_SIZES = {'location': 3, 'rotation_euler': 3, 'rotation_quaternion': 4,
          'rotation_axis_angle': 4, 'scale': 3, 'delta_location': 3,
          'delta_rotation_euler': 3, 'delta_rotation_quaternion': 4, 'delta_scale': 3}
_POSE_SETTINGS = ('ik_stretch', 'ik_rotation_weight', 'ik_linear_weight',
                  'use_ik_linear_control', 'use_ik_rotation_control',
                  'lock_ik_x', 'lock_ik_y', 'lock_ik_z',
                  'use_ik_limit_x', 'use_ik_limit_y', 'use_ik_limit_z',
                  'ik_min_x', 'ik_min_y', 'ik_min_z', 'ik_max_x', 'ik_max_y', 'ik_max_z',
                  'ik_stiffness_x', 'ik_stiffness_y', 'ik_stiffness_z')


def _require(value, message):
    if not value:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _plain(value):
    if type(value) in (bool, int, str):
        return value
    if type(value) is float:
        _require(math.isfinite(value), 'Export base pose contains a non-finite input.')
        return value
    if hasattr(value, '__len__') and hasattr(value, '__getitem__') and not hasattr(value, 'keys'):
        _require(len(value) <= MAX_CURVES, 'Export input array exceeds the supported bound.')
        return [_plain(item) for item in (sorted(value) if isinstance(value, set) else value)]
    raise ValueError('Unsupported export base-pose input type: ' + type(value).__name__)


def _curves(action, handle):
    if action.is_action_layered:
        _require(len(action.layers) == 1, 'Bake multiple Action layers before capturing export inputs.')
        layer = action.layers[0]
        _require(not getattr(layer, 'mute', False) and getattr(layer, 'influence', 1.) == 1.
                 and getattr(layer, 'mix_mode', 'REPLACE') == 'REPLACE'
                 and len(layer.strips) == 1 and layer.strips[0].type == 'KEYFRAME',
                 'Muted, weighted or mixed Action layers need a baked export input contract.')
        slots = [slot for slot in action.slots if slot.handle == handle and slot.target_id_type == 'OBJECT']
        _require(len(slots) == 1, 'Choose the intended Object Action slot before capturing export inputs.')
        bags = [bag for bag in layer.strips[0].channelbags if bag.slot_handle == handle]
        _require(len(bags) == 1 and len(bags[0].fcurves) <= MAX_CURVES,
                 'Choose one bounded Object channelbag for export inputs.')
        return list(bags[0].fcurves)
    _require(handle in (None, 0), 'Legacy Action has no Object slot.')
    _require(len(action.fcurves) <= MAX_CURVES, 'Action exceeds the export input curve bound.')
    return list(action.fcurves)


def _handle(rig, action, handle):
    if not action.is_action_layered:
        return 0
    if handle is None:
        ad = rig.animation_data
        _require(ad is not None and ad.action is action and ad.action_slot is not None,
                 'Activate the intended Action and slot before capturing export inputs.')
        handle = ad.action_slot.handle
    return handle


def _settings(block):
    result = {}
    for prop in block.bl_rna.properties:
        if (prop.identifier == 'rna_type' or prop.is_readonly
                or prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'ENUM', 'STRING'}):
            continue
        result[prop.identifier] = _plain(getattr(block, prop.identifier))
    return result


def _no_nested_inputs(block):
    for prop in block.bl_rna.properties:
        if prop.type == 'COLLECTION':
            _require(len(getattr(block, prop.identifier)) == 0,
                     'Nested constraint/driver inputs need a separately baked export input contract.')


def _target_identity(value, rig):
    if value is None:
        return None
    _require(value is rig or value is rig.data,
             'Export base pose supports same-rig dependencies only; bake external inputs first.')
    return 'rig' if value is rig else 'data'


def _constraint_shape(constraint, rig):
    _no_nested_inputs(constraint)
    result = {'type': constraint.type, 'name': constraint.name, 'settings': _settings(constraint)}
    for prop in constraint.bl_rna.properties:
        if prop.type == 'POINTER' and prop.identifier != 'rna_type':
            value = getattr(constraint, prop.identifier)
            if value is not None and hasattr(value, 'as_pointer'):
                result[prop.identifier] = _target_identity(value, rig)
    return result


def _driver_shape(rig):
    result, driven = [], set()
    for owner, prefix in ((rig, ''), (rig.data, 'data.')):
        ad = owner.animation_data
        if owner is rig.data:
            _require(ad is None, 'Armature-data animation needs a separately baked export input contract.')
        _require(ad is None or len(ad.drivers) <= MAX_CURVES, 'Rig exceeds the export driver bound.')
        for curve in ad.drivers if ad else ():
            _require(len(curve.keyframe_points) + len(curve.sampled_points) <= MAX_CURVES,
                     'Mapped driver curves exceed the supported export input bound.')
            for modifier in curve.modifiers:
                _no_nested_inputs(modifier)
            driver = curve.driver
            _require(not driver.use_self, 'Self-referencing driver needs a separately baked export input contract.')
            _require(driver.type != 'SCRIPTED' or driver.is_simple_expression,
                     'Non-simple driver expression cannot use an export base pose; bake it first.')
            variables = []
            for variable in driver.variables:
                targets = []
                for target in variable.targets:
                    identity = _target_identity(target.id, rig)
                    _require(identity is not None, 'An export driver input is missing its same-rig target.')
                    targets.append({'id': identity, 'settings': _settings(target)})
                variables.append({'name': variable.name, 'type': variable.type, 'targets': targets})
            result.append({'path': prefix + curve.data_path, 'index': curve.array_index,
                           'mute': curve.mute, 'type': driver.type, 'expression': driver.expression,
                           'variables': variables, 'curve_settings': _settings(curve),
                           'keys': [_settings(key) for key in curve.keyframe_points],
                           'samples': [_settings(point) for point in curve.sampled_points],
                           'modifiers': [_settings(mod) for mod in curve.modifiers]})
            if not curve.mute:
                driven.add((prefix + curve.data_path, curve.array_index))
    return result, driven


def _inventory(rig, action, handle):
    _require(rig is not None and rig.type == 'ARMATURE' and not rig.library and not rig.data.library,
             'Export base pose needs a local Armature.')
    _require(action is not None and not action.library and rig.mode != 'EDIT',
             'Choose a local editable Action outside Edit Mode.')
    _require(rig.parent is None and not rig.constraints and not rig.modifiers,
             'Parented rigs or object constraints/modifiers need a separately baked export input contract.')
    _require(rig.data.pose_position == 'POSE', 'Return to Pose display before capturing or exporting animation inputs.')
    _require(0 < len(rig.pose.bones) <= MAX_BONES, 'Rig exceeds the supported export bone bound.')
    _require(sum(len(pb.constraints) for pb in rig.pose.bones) <= MAX_CURVES,
             'Rig exceeds the export constraint bound.')
    handle = _handle(rig, action, handle)
    curves = _curves(action, handle)
    for curve in curves:
        if not curve.mute:
            _require(curve.keyframe_points or curve.sampled_points,
                     'Empty enabled Action curves need authored samples before capturing export inputs.')
            _require(not getattr(getattr(curve, 'group', None), 'mute', False),
                     'Muted Action groups need a baked export input contract.')
    keyed = {(curve.data_path, curve.array_index) for curve in curves if not curve.mute}
    _require(keyed, 'The selected Action slot has no enabled channels.')
    drivers, driven = _driver_shape(rig)
    blocked = keyed | driven
    inputs, transforms, main_missing, has_custom = [], 0, 0, False
    delta_neutral = True

    def add(path, value, *, transform=False, main=False):
        nonlocal transforms, main_missing
        value = _plain(value)
        if isinstance(value, list):
            for index, component in enumerate(value):
                if (path, index) not in blocked:
                    inputs.append({'path': path, 'index': index, 'value': component})
                    transforms += int(transform)
                    main_missing += int(main)
        elif (path, 0) not in blocked:
            inputs.append({'path': path, 'index': None, 'value': value})
        _require(len(inputs) <= MAX_INPUTS, 'Rig exceeds the export input bound.')

    def custom(block, prefix):
        nonlocal has_custom
        _require(len(block.keys()) <= MAX_CURVES, 'Rig has too many custom-property inputs.')
        for key in sorted(block.keys()):
            if key in {RECORD, COUNT, '_RNA_UI'}:
                continue
            value = block[key]
            if type(value) not in (int, float, bool) and not type(value).__name__.startswith('IDPropertyArray'):
                continue
            path = prefix + '[' + json.dumps(key, ensure_ascii=False) + ']'
            size = len(value) if type(value).__name__.startswith('IDPropertyArray') else 1
            has_custom |= any((path, index) not in blocked for index in range(size))
            add(path, value)

    def pose(block, prefix, *, object_block=False):
        nonlocal delta_neutral
        add(prefix + 'rotation_mode', block.rotation_mode)
        rotation = ('rotation_quaternion' if block.rotation_mode == 'QUATERNION' else
                    'rotation_axis_angle' if block.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        fields = ['location', rotation, 'scale']
        if object_block:
            fields += ['delta_location', 'delta_scale', 'delta_rotation_quaternion'
                       if block.rotation_mode == 'QUATERNION' else 'delta_rotation_euler']
        for field in fields:
            add(prefix + field, getattr(block, field), transform=True, main=not field.startswith('delta_'))
            if field.startswith('delta_'):
                expected = ([1., 1., 1.] if field == 'delta_scale' else
                            [1., 0., 0., 0.] if field == 'delta_rotation_quaternion' else [0., 0., 0.])
                delta_neutral &= all((prefix + field, index) in blocked or value == expected[index]
                                     for index, value in enumerate(getattr(block, field)))
        custom(block, prefix.removesuffix('.'))

    pose(rig, '', object_block=True)
    custom(rig.data, 'data')
    bones = []
    for pb in rig.pose.bones:
        prefix = pb.path_from_id() + '.'
        pose(pb, prefix)
        for name in _POSE_SETTINGS:
            if hasattr(pb, name):
                add(prefix + name, getattr(pb, name))
        constraints = []
        for constraint in pb.constraints:
            shape = _constraint_shape(constraint, rig)
            # Constraint configuration is structural. Its keyed/driven values
            # can vary, while other changes deliberately require recapture.
            for name in list(shape['settings']):
                path = constraint.path_from_id() + '.' + name
                if (path, 0) in blocked:
                    del shape['settings'][name]
            constraints.append(shape)
        bone = pb.bone
        custom(bone, 'data.' + bone.path_from_id())
        bones.append({'name': pb.name, 'parent': bone.parent.name if bone.parent else None,
                      'rest': [float(v) for row in bone.matrix_local for v in row],
                      'connect': bone.use_connect, 'deform': bone.use_deform,
                      'inherit_scale': bone.inherit_scale,
                      'inherit_rotation': bone.use_inherit_rotation,
                      'local_location': bone.use_local_location,
                      'relative_parent': bone.use_relative_parent, 'constraints': constraints})
    inputs.sort(key=lambda row: (row['path'], -1 if row['index'] is None else row['index']))
    structure = {'bones': bones, 'drivers': drivers, 'keyed': sorted([list(row) for row in keyed]),
                 'inputs': [[row['path'], row['index'], type(row['value']).__name__] for row in inputs]}
    return {'slot': handle, 'structure': _digest(structure), 'inputs': inputs,
            'unkeyed_components': transforms,
            '_fully_keyed': main_missing == 0 and delta_neutral and not has_custom and not drivers
                            and not any(pb.constraints for pb in rig.pose.bones)}


def status(rig, action, slot_handle=None):
    """Bounded saved-metadata status for drawing; validity is checked on export."""
    if rig is None or action is None or getattr(action, 'library', None):
        return {'state': 'UNSUPPORTED', 'message': 'Choose an editable Custom Action.',
                'unkeyed_components': 0, 'has_baseline': False}
    value = action.get(RECORD)
    present = isinstance(value, str) and 0 < len(value) <= MAX_RECORD
    count = action.get(COUNT, 0)
    return {'state': 'READY' if present else 'CAPTURE_REQUIRED',
            'message': 'Captured; checked when exporting.' if present else 'Capture the intended export base pose.',
            'unkeyed_components': count if type(count) is int and 0 <= count < 100000 else 0,
            'has_baseline': present}


def capture(rig, action, slot_handle=None):
    ad = rig.animation_data if rig is not None else None
    _require(ad is not None and ad.action is action and not ad.use_tweak_mode,
             'Activate the intended Custom Action outside NLA Tweak Mode before capture.')
    _require(not action.is_action_layered or ad.action_slot is not None
             and slot_handle in (None, ad.action_slot.handle), 'The active Custom Action slot changed.')
    inventory = _inventory(rig, action, slot_handle)
    inventory.pop('_fully_keyed')
    record = {'schema': SCHEMA, **inventory}
    record['sha256'] = _digest(record)
    encoded = _json(record)
    _require(len(encoded) <= MAX_RECORD, 'Export base pose is too large; use a smaller supported rig.')
    old, count = action.get(RECORD), action.get(COUNT)
    try:
        action[RECORD], action[COUNT] = encoded, inventory['unkeyed_components']
    except Exception:
        for key, value in ((RECORD, old), (COUNT, count)):
            if value is None:
                if key in action:
                    del action[key]
            else:
                action[key] = value
        raise
    return record


def clear(context, rig, action):
    """Explicit metadata-only authoring; require the same protected Custom owner."""
    from .animation import _export_base_owner
    _export_base_owner(context, rig, action)
    previous = {key: action[key] for key in (RECORD, COUNT) if key in action}
    try:
        for key in previous:
            del action[key]
    except Exception:
        for key, value in previous.items():
            action[key] = value
        raise
    return bool(previous)


def _read(action):
    raw = action.get(RECORD)
    _require(isinstance(raw, str) and 0 < len(raw) <= MAX_RECORD,
             'Capture Export Base Pose for this sparse Action before exporting.')
    try:
        value = json.loads(raw)
        _require(type(value) is dict, 'Saved export base pose must be an object.')
        expected = value.pop('sha256')
        _require(type(value) is dict and value.get('schema') == SCHEMA and expected == _digest(value),
                 'Saved export base pose is invalid; capture it again deliberately.')
        _require(set(value) == {'schema', 'slot', 'structure', 'inputs', 'unkeyed_components'},
                 'Saved export base pose has an unsupported schema.')
        _require(type(value['inputs']) is list and len(value['inputs']) <= MAX_INPUTS,
                 'Saved export base pose exceeds the input bound.')
        value['sha256'] = expected
        return value
    except (TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError('Saved export base pose is invalid; capture it again deliberately.') from error


def prepare(rig, action, slot_handle=None, frame_start=None):
    inventory = _inventory(rig, action, slot_handle)
    fully_keyed = inventory.pop('_fully_keyed')
    retained = RECORD in action
    if retained:
        record = _read(action)
    else:
        _require(fully_keyed, 'Capture Export Base Pose for this sparse Action before exporting.')
        record = {'schema': SCHEMA, **inventory}
        record['sha256'] = _digest(record)
    _require(record['slot'] == inventory['slot'] and record['structure'] == inventory['structure'],
             'Rig structure, Action channels or export input schema changed; capture Export Base Pose again.')
    _require([[v['path'], v['index'], type(v['value']).__name__] for v in record['inputs']] ==
             [[v['path'], v['index'], type(v['value']).__name__] for v in inventory['inputs']],
             'Saved export base-pose channels are incompatible; capture them again.')
    _require(type(frame_start) in (int, float) and math.isfinite(frame_start),
             'Export needs a finite fixed reference frame.')
    return {**record, 'reference_frame': float(frame_start), 'rig': rig.name, 'action': action.name,
            'retained': retained}


def apply(rig, action, sealed):
    """Disposable worker only: seed saved unkeyed inputs, never insert keys."""
    _require(type(sealed) is dict and sealed.get('rig') == rig.name and sealed.get('action') == action.name,
             'Export input contract identifies another rig or Action.')
    original = {key: value for key, value in sealed.items()
                if key not in {'reference_frame', 'rig', 'action', 'retained'}}
    if sealed.get('retained') is True:
        _require(_json(original) == action.get(RECORD), 'Snapshot export base pose differs from its sealed job.')
    else:
        _require(sealed.get('retained') is False and RECORD not in action,
                 'Fully keyed export unexpectedly gained a saved base pose.')
    verified = prepare(rig, action, sealed['slot'], sealed['reference_frame'])
    _require(verified == sealed, 'Snapshot export input contract differs from the captured baseline.')
    ad = rig.animation_data_create()
    ad.action = action
    if action.is_action_layered:
        ad.action_slot = next(slot for slot in action.slots if slot.handle == sealed['slot'])
    ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.
    ad.action_extrapolation = 'HOLD'
    for row in sealed['inputs']:
        path, index, value = row['path'], row['index'], row['value']
        if index is not None:
            rig.path_resolve(path)[index] = value
        elif path.endswith(']'):
            # All generated scalar custom-property paths end with a JSON key.
            split = path.rfind('[')
            block = rig.path_resolve(path[:split]) if split else rig
            block[json.loads(path[split + 1:-1])] = value
        else:
            block_path, _, name = path.rpartition('.')
            block = rig.path_resolve(block_path) if block_path else rig
            setattr(block, name, value)
    return {'schema': SCHEMA, 'sha256': sealed['sha256'], 'reference_frame': sealed['reference_frame'],
            'unkeyed_components': sealed['unkeyed_components'],
            'policy': 'captured-action-base-pose' if sealed['retained'] else 'fully-keyed-neutral-deltas'}
