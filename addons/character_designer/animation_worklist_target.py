"""Persistent, reversible Worklist adoption of the artist's existing Main Rig.

Only the supported Original-to-Controls operation changes the control mode.
Recovery keeps ID references in Blender and validates its complete record,
Rest and live control graph before modifying the artist again.
"""
from __future__ import annotations

import json
import math
import hashlib
from dataclasses import dataclass

import bpy

SESSION = 'character_designer_worklist_main_session_v1'
REFS = 'character_designer_worklist_main_refs_v1'
RESTORED = 'character_designer_worklist_main_restored_v1'
ACTION_MOTION_KEY = 'character_designer_worklist_motion_rest_v1'
ACTION_RIG_KEY = 'character_designer_worklist_motion_rig_v1'
_LOCKS = ('lock_location', 'lock_rotation', 'lock_scale', 'lock_rotation_w', 'lock_rotations_4d')
_CONSTRAINT_FIELDS = ('mute', 'influence', 'mix_mode')


def is_adopted(rig):
    return bool(rig is not None and hasattr(rig, 'get') and rig.get(SESSION) is not None)


def managed(rig):
    return bool(rig is not None and hasattr(rig, 'get')
                and (rig.get(SESSION) is not None or rig.get(RESTORED) is not None))


def _encode(value, references):
    if isinstance(value, bpy.types.ID):
        key = 'id_' + str(len(references))
        references[key] = value
        return {'$id': key}
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('A Main Rig recovery value is not finite.')
        return value
    if hasattr(value, 'items'):
        return {str(key): _encode(entry, references) for key, entry in value.items()}
    if hasattr(value, '__iter__'):
        return [_encode(entry, references) for entry in value]
    raise ValueError('Unsupported Main Rig recovery value: ' + type(value).__name__)


def _decode(value, references):
    if isinstance(value, dict):
        if set(value) == {'$id'}:
            target = references.get(value['$id'])
            if target is None:
                raise ValueError('A saved Main Rig recovery data block was removed.')
            return target
        return {key: _decode(entry, references) for key, entry in value.items()}
    if isinstance(value, list):
        return [_decode(entry, references) for entry in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('A saved Main Rig recovery value is not finite.')
    return value


def _freeze(value):
    """Detach IDProperty containers before Original.leave deletes their keys."""
    references = {}
    return _decode(_encode(value, references), references)


def _metadata_keys():
    from . import animation_link as links, body_original_mode as original
    from . import bone_display as display, bone_collections as groups, skirt_original_mode
    return (original.SESSION, original.DISPLAY_REFS, original.DISPLAY_OWNER,
            display.VIEW_KEY, display.REFS_KEY, groups.BACKUP_KEY,
            skirt_original_mode.CORRECTIONS, links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY)


def _numeric_properties(target):
    return {pb.name: {key: value for key, value in pb.items()
                     if type(value) in (int, float) and math.isfinite(value)}
            for pb in target.pose.bones}


def _capture(context, rig):
    from . import unity_animation as ua
    from . import bone_display as display, skirt_rig
    targets = list(dict.fromkeys(display._affected(context, rig)))
    if rig not in targets:
        targets.insert(0, rig)
    metadata = list(dict.fromkeys(targets + [target.data for target in targets]
                                  + list(skirt_rig.shared_sources(rig))))
    keys = _metadata_keys()
    records = []
    for target in targets:
        records.append({'target': target, 'data': target.data,
                        'data_fake_user': bool(target.data.use_fake_user),
                        'rest': ua._rest_state(target.data),
                        'snapshot': ua._snapshot(context, target),
                        'action': target.animation_data.action if target.animation_data else None,
                        'action_fake_user': bool(target.animation_data.action.use_fake_user)
                            if target.animation_data and target.animation_data.action else False,
                        'display': display._snapshot(target),
                        'properties': _numeric_properties(target),
                        'locks': {pb.name: {field: (list(getattr(pb, field))
                                  if hasattr(getattr(pb, field), '__len__') else getattr(pb, field))
                                  for field in _LOCKS} for pb in target.pose.bones},
                        'constraints': {pb.name: {constraint.name: {'type': constraint.type,
                            **{field: getattr(constraint, field) for field in _CONSTRAINT_FIELDS
                               if hasattr(constraint, field)}} for constraint in pb.constraints}
                            for pb in target.pose.bones}})
    active = context.view_layer.objects.active
    return _freeze({'targets': records,
            'metadata': [{'owner': owner, 'values': {key: owner[key] for key in keys if key in owner}}
                         for owner in metadata],
            'context': {'active': active, 'selected': list(context.selected_objects),
                        'mode': active.mode if active else 'OBJECT'}})


def _validate_capture(context, captured):
    from . import unity_animation as ua
    from . import bone_display as display, body_original_mode as original
    if not isinstance(captured, dict) or set(captured) != {'targets', 'metadata', 'context'}:
        raise ValueError('The saved Main Rig recovery snapshot is incomplete.')
    if not captured['targets'] or not captured['metadata']:
        raise ValueError('The saved Main Rig recovery targets are incomplete.')
    for entry in captured['targets']:
        if (not isinstance(entry, dict) or set(entry) != {'target', 'data', 'data_fake_user', 'rest',
                'snapshot', 'action', 'action_fake_user', 'display', 'properties', 'locks', 'constraints'}):
            raise ValueError('The saved Main Rig target record is incomplete.')
        target, data, snapshot = entry['target'], entry['data'], entry['snapshot']
        if (not isinstance(target, bpy.types.Object) or target.type != 'ARMATURE'
                or not isinstance(data, bpy.types.Armature)
                or target.library or data.library or target.override_library
                or target.name not in context.scene.objects
                or target.name not in context.view_layer.objects):
            raise ValueError('Restore every saved Main Rig target in its original scene/view layer first.')
        if not ua._matches_rest_state(data, entry['rest']):
            raise ValueError('The saved artist Rest skeleton changed; Main Rig recovery left it untouched.')
        names = set(data.bones.keys())
        current_properties = _numeric_properties(target)
        expected_snapshot = {'had_animation_data', 'had_action', 'slot', 'use_nla', 'blend', 'influence',
                             'extrapolation', 'pose', 'fps', 'fps_base', 'frame', 'start', 'end',
                             'preview_start', 'preview_end', 'use_preview', 'autokey', 'playing'}
        if (not isinstance(snapshot, dict) or set(snapshot) != expected_snapshot
                or any(type(snapshot[key]) is not bool for key in ('had_animation_data', 'had_action',
                       'use_nla', 'use_preview', 'autokey', 'playing'))
                or any(type(snapshot[key]) not in (int, float) or not math.isfinite(snapshot[key])
                       for key in ('slot', 'influence', 'fps', 'fps_base', 'frame', 'start', 'end',
                                   'preview_start', 'preview_end'))
                or snapshot['fps'] <= 0 or snapshot['fps_base'] <= 0
                or snapshot['blend'] not in {'REPLACE', 'ADD', 'SUBTRACT', 'MULTIPLY', 'COMBINE'}
                or snapshot['extrapolation'] not in {'NOTHING', 'HOLD', 'HOLD_FORWARD'}
                or type(entry['data_fake_user']) is not bool or type(entry['action_fake_user']) is not bool):
            raise ValueError('The saved Main Rig Action/timing snapshot is incomplete or invalid.')
        if (set(snapshot['pose']) != names or set(entry['properties']) != names
                or set(entry['locks']) != names or set(entry['constraints']) != names):
            raise ValueError('The saved Main Rig bone snapshot is incomplete.')
        previous = entry['action']
        if bool(previous) != bool(snapshot['had_action']):
            raise ValueError('The saved Main Rig Action reference is incomplete.')
        if previous and not any(slot.handle == snapshot['slot'] for slot in previous.slots):
            raise ValueError('The saved Main Rig Action slot was removed.')
        display._check_object_restore(target, entry['display'])
        for name, fields in snapshot['pose'].items():
            if (set(fields) != {'location', 'rotation_quaternion', 'rotation_euler',
                               'rotation_axis_angle', 'scale', 'rotation_mode'}
                    or any(len(fields[prop]) != (4 if prop in {'rotation_quaternion', 'rotation_axis_angle'} else 3)
                           or any(type(value) not in (int, float) or not math.isfinite(value)
                                  for value in fields[prop]) for prop in fields if prop != 'rotation_mode')):
                raise ValueError('The saved Main Rig pose channels are invalid.')
            pb = target.pose.bones[name]
            if (fields['rotation_mode'] not in {'QUATERNION', 'AXIS_ANGLE', 'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
                    or set(entry['locks'][name]) != set(_LOCKS)
                    or set(entry['properties'][name]) != set(current_properties[name])
                    or any(not isinstance(entry['properties'][name], dict)
                           or type(value) not in (int, float) or not math.isfinite(value)
                           for value in entry['properties'][name].values())
                    or any(not isinstance(entry['locks'][name][field], list)
                           or len(entry['locks'][name][field]) != 3
                           or any(type(value) is not bool for value in entry['locks'][name][field])
                           for field in ('lock_location', 'lock_rotation', 'lock_scale'))
                    or any(type(entry['locks'][name][field]) is not bool
                           for field in ('lock_rotation_w', 'lock_rotations_4d'))):
                raise ValueError('The saved Main Rig control properties or locks are invalid.')
            if set(entry['constraints'][name]) != set(pb.constraints.keys()):
                raise ValueError('Main Rig constraints changed; recovery left the current graph untouched.')
            if any(pb.constraints[key].type != value['type']
                   for key, value in entry['constraints'][name].items()):
                raise ValueError('A saved Main Rig constraint changed type.')
            if any(key not in pb for key in entry['properties'][name]):
                raise ValueError('A saved Main Rig control property was removed.')
    keys = set(_metadata_keys())
    if any(not isinstance(entry['owner'], bpy.types.ID) or set(entry['values']) - keys
           for entry in captured['metadata']):
        raise ValueError('The saved Main Rig metadata recovery record is invalid.')
    for entry in captured['metadata']:
        if original.SESSION not in entry['values']:
            continue
        target = entry['owner']
        saved_original = json.loads(entry['values'][original.SESSION])
        saved_data = next(value['data'] for value in captured['targets'] if value['target'] is target)
        native = {name: {'matrix': [list(row) for row in saved_data.bones[name].matrix_local],
                        'length': saved_data.bones[name].length,
                        'parent': saved_data.bones[name].parent.name if saved_data.bones[name].parent else None,
                        'connected': saved_data.bones[name].use_connect,
                        'inherit_scale': saved_data.bones[name].inherit_scale,
                        'inherit_rotation': saved_data.bones[name].use_inherit_rotation,
                        'local_location': saved_data.bones[name].use_local_location}
                  for name in saved_original['rest']}
        original._validate_session_rest(target, saved_original, current=native)


def _apply_capture(context, rig, captured):
    from . import unity_animation as ua
    from . import bone_display as display, body_original_mode as original, bone_collections as groups
    ua._set_playing(context, False)
    for entry in captured['targets']:
        target = entry['target']
        if target.data is not entry['data']:
            ua._swap_armature_data(context, target, entry['data'])
    for entry in captured['metadata']:
        owner, values = entry['owner'], entry['values']
        for key in _metadata_keys():
            if key in values:
                owner[key] = values[key]
            elif key in owner:
                del owner[key]
    for entry in captured['targets']:
        target = entry['target']
        for name, properties in entry['properties'].items():
            pb = target.pose.bones[name]
            for key, value in properties.items():
                pb[key] = value
            for field, value in entry['locks'][name].items():
                setattr(pb, field, value)
            for key, values in entry['constraints'][name].items():
                constraint = pb.constraints[key]
                for field, value in values.items():
                    if field != 'type':
                        setattr(constraint, field, value)
        ua._restore_snapshot(context, target, entry['snapshot'], entry['action'])
        display._restore(target, entry['display'])
        target.data.use_fake_user = entry['data_fake_user']
        if entry['action']:
            if entry['action'].library is None:
                entry['action'].use_fake_user = entry['action_fake_user']
    context.view_layer.update()
    if original.active(rig):
        original._validate_session_rest(rig)
    groups._FRAME_CACHE.pop(rig.as_pointer(), None)
    display._completed(rig, update=False)
    previous = captured['context']
    active = context.view_layer.objects.active
    if active and active.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for target in context.selected_objects:
        target.select_set(False)
    for target in previous['selected']:
        if context.view_layer.objects.get(target.name) is target:
            target.select_set(True)
    active = previous['active']
    context.view_layer.objects.active = active
    if active and previous['mode'] != 'OBJECT':
        bpy.ops.object.mode_set(mode=previous['mode'])
    context.view_layer.update()


def _store(rig, record):
    references = {}
    encoded = _encode(record, references)
    encoded['capture_sha256'] = hashlib.sha256(json.dumps(
        encoded['capture'], ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    raw = json.dumps(encoded, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    rig[REFS] = references
    rig[SESSION] = raw


def _load(rig):
    try:
        encoded = json.loads(rig[SESSION])
        if not isinstance(encoded, dict) or 'capture' not in encoded or 'capture_sha256' not in encoded:
            raise ValueError('The saved Main Rig adoption record is incomplete.')
        digest = hashlib.sha256(json.dumps(encoded['capture'], ensure_ascii=False,
            allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        if digest != encoded.pop('capture_sha256'):
            raise ValueError('The saved Main Rig recovery snapshot changed or was truncated.')
        record = _decode(encoded, rig.get(REFS, {}))
        if (not isinstance(record, dict) or record.get('version') != 1
                or set(record) != {'version', 'rig', 'scene', 'view_layer', 'workspace_identity',
                                  'capture', 'packet_path', 'packet_sha256', 'motion_data', 'motion_rest',
                                  'mapped_names'}
                or record['rig'] is not rig):
            raise ValueError('The saved Main Rig adoption record is incomplete.')
        if record['capture']['targets'][0]['target'] is not rig:
            raise ValueError('The saved Main Rig recovery snapshot belongs to another rig.')
        return record
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('The saved Main Rig adoption record is unreadable; no changes were made.') from exc


def capture_session_metadata(rig):
    return {key: (dict(rig[key]) if key == REFS else rig[key])
            for key in (SESSION, REFS, RESTORED) if key in rig}


def restore_session_metadata(rig, saved):
    for key in (SESSION, REFS, RESTORED):
        if key in saved:
            rig[key] = saved[key]
        elif key in rig:
            del rig[key]


@dataclass(frozen=True)
class _MotionRestView:
    original_data: object
    motion_data: object
    names: frozenset

    def _original(self, bone):
        if (bone is None or getattr(bone, 'id_data', None) is not self.motion_data
                or bone.name not in self.names):
            return None
        return self.original_data.bones.get(bone.name)

    def source_bone(self, bone):
        original = self._original(bone)
        if original is None:
            raise ValueError('The source bone is outside the validated Worklist motion data.')
        return original

    def matches(self, bone, state):
        from . import limb_ik
        return limb_ik._rest_state_matches(self._original(bone), state)

    def details(self, bone, state):
        from . import limb_ik
        original = self._original(bone)
        if original is None:
            return 'bone is outside the validated Worklist motion data'
        return limb_ik._rest_state_mismatch_details(original, state)


def _validated_names(names):
    if (not isinstance(names, list) or not names or len(names) > 1024
            or any(not isinstance(name, str) or not name for name in names)
            or names != sorted(set(names))):
        raise ValueError('The saved Main Rig mapped bone inventory is malformed.')
    return frozenset(names)


def _prove_motion_data(original, motion, original_rest, motion_rest, mapped_names):
    from . import unity_animation as ua, limb_ik, skirt_rig
    if (not ua._matches_rest_state(original, original_rest)
            or not ua._matches_rest_state(motion, motion_rest)):
        raise ValueError('The saved artist or owned motion-copy Rest changed; no validation exception was granted.')
    names = frozenset(mapped_names)
    if set(motion.bones.keys()) != set(original.bones.keys()) or not names.issubset(original.bones.keys()):
        raise ValueError('The owned motion copy changed the artist bone inventory.')
    # Exported Hair source chains may carry hair_bones ownership. They are
    # authored packet bones, whereas generated controls and Dress are not.
    owner_keys = (limb_ik.OWNER_KEY, skirt_rig.OWNER_KEY)
    translated = set()
    for bone in motion.bones:
        protected = original.bones[bone.name]
        if not ua._same_rest_bone(protected, bone):
            raise ValueError('The owned motion copy changed Rest geometry, parents or inheritance: ' + bone.name)
        if bool(bone.use_connect) != bool(protected.use_connect):
            if (not protected.use_connect or bone.use_connect or bone.name not in names
                    or any(protected.get(key) is not None or bone.get(key) is not None for key in owner_keys)):
                raise ValueError('An unapproved connected-bone flag changed in the motion copy: ' + bone.name)
            translated.add(bone.name)
    return frozenset(translated)


def _validation_rest_view(rig, mapped_names, record):
    artist = record['capture']['targets'][0]
    original, motion = artist['data'], rig.data
    if motion is original:
        return None
    if motion is not record['motion_data']:
        raise ValueError('The saved artist or owned motion-copy Rest changed; no validation exception was granted.')
    _prove_motion_data(original, motion, artist['rest'], record['motion_rest'], mapped_names)
    return _MotionRestView(original, motion, frozenset(original.bones.keys()))


def validation_rest_view(rig, mapped_names):
    """Expose protected Rest only for the exact validated translation copy.

    Every normal target keeps the ordinary validator. The copy may differ
    solely by connected editing flags on mapped native Body/Hair bones; full
    artist/copy Rest and ownership are proven before constructing this view.
    This does not call mapping/ensure_active and cannot recurse through them.
    """
    if not is_adopted(rig):
        return None
    return _validation_rest_view(rig, mapped_names, _load(rig))


def control_rest_view(rig):
    """Authenticate the saved source Rest for ordinary control/UI validation.

    Adoption records the fully checked packet mapping once; control checks
    need neither packet I/O nor recursive animation mapping to use it.
    """
    if not is_adopted(rig):
        return None
    record = _load(rig)
    names = record['mapped_names']
    _validated_names(names)
    original = record['capture']['targets'][0]['data']
    if (not set(names).issubset(original.bones.keys())
            or not set(names).issubset(rig.data.bones.keys())):
        raise ValueError('The saved Main Rig mapped bones no longer belong to its protected/current data.')
    return _validation_rest_view(rig, names, record)


def _action_motion_keys():
    from . import unity_animation as ua
    return (ACTION_MOTION_KEY, ACTION_RIG_KEY, ua.ORIGINAL_DATA_KEY,
            ua.PREVIEW_DATA_KEY, ua.ORIGINAL_REST_KEY)


def _capture_action_motion_refs(items):
    return [(item.custom_action, _freeze({key: item.custom_action[key]
            for key in _action_motion_keys() if key in item.custom_action})) for item in items]


def _restore_action_motion_refs(captured):
    for action, values in captured:
        for key in _action_motion_keys():
            if key in values:
                action[key] = values[key]
            elif key in action:
                del action[key]


def attach_action_motion_data(rig, action):
    """Keep translation proof on a local draft, never in a Source library."""
    from . import unity_animation as ua
    if not is_adopted(rig):
        return
    if action is None or action.library is not None:
        raise ValueError('Only a local Custom Action can retain Main Rig motion-data recovery.')
    record = _load(rig)
    artist = record['capture']['targets'][0]
    if rig.data is not record['motion_data']:
        raise ValueError('Main Rig motion data changed before its local Action recovery was recorded.')
    _validated_names(record['mapped_names'])
    _prove_motion_data(artist['data'], rig.data, artist['rest'], record['motion_rest'], record['mapped_names'])
    original_raw = json.dumps(artist['rest'], allow_nan=False, sort_keys=True, separators=(',', ':'))
    proof = {'version': 1, 'mapped_names': record['mapped_names'], 'motion_rest': record['motion_rest'],
             'original_rest_sha256': hashlib.sha256(original_raw.encode('utf-8')).hexdigest()}
    action[ACTION_RIG_KEY] = rig
    action[ua.ORIGINAL_DATA_KEY], action[ua.PREVIEW_DATA_KEY] = artist['data'], rig.data
    action[ua.ORIGINAL_REST_KEY] = original_raw
    action[ACTION_MOTION_KEY] = json.dumps(proof, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _read_action_motion_data(rig, action, original):
    from . import unity_animation as ua
    if (action is None or action.library is not None or action.get(ACTION_RIG_KEY) is not rig
            or action.get(ua.ORIGINAL_DATA_KEY) is not original):
        raise ValueError('A retained Custom Action lost its original Main Rig motion-data references.')
    try:
        proof = _decode(json.loads(action[ACTION_MOTION_KEY]), {})
        original_raw = action[ua.ORIGINAL_REST_KEY]
        original_rest = _decode(json.loads(original_raw), {})
        motion = action[ua.PREVIEW_DATA_KEY]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('A retained Custom Action motion-data recovery record is unreadable.') from exc
    if (not isinstance(proof, dict) or set(proof) != {'version', 'mapped_names', 'motion_rest',
            'original_rest_sha256'} or proof['version'] != 1
            or hashlib.sha256(original_raw.encode('utf-8')).hexdigest() != proof['original_rest_sha256']
            or not isinstance(motion, bpy.types.Armature) or motion.library is not None):
        raise ValueError('A retained Custom Action motion-data recovery record is invalid.')
    names = _validated_names(proof['mapped_names'])
    translated = _prove_motion_data(original, motion, original_rest, proof['motion_rest'], names)
    return names, motion, translated


def _retained_motion_data(rig, items):
    """Read-only proof of the editing flags needed by retained local drafts."""
    from . import animation_worklist as worklist
    if not items or not rig.get(RESTORED):
        return None
    translated, mapped = set(), None
    for item in items:
        action = item.custom_action
        names, motion, changed = _read_action_motion_data(rig, action, rig.data)
        worklist._slot(action, item.custom_slot)
        worklist._check_baseline(item)
        if mapped is not None and mapped != names:
            raise ValueError('Retained Custom Actions disagree about their validated Main Rig bone inventory.')
        mapped = names
        translated.update(changed)
    return {'translated_names': frozenset(translated), 'mapped_names': mapped}


def _contains_data_reference(value, data):
    if isinstance(value, bpy.types.ID):
        return value is data
    if hasattr(value, 'items'):
        return any(_contains_data_reference(entry, data) for entry in value.values())
    if not isinstance(value, (str, bytes)) and hasattr(value, '__iter__'):
        return any(_contains_data_reference(entry, data) for entry in value)
    return False


def _data_reference_keys(owner, data):
    return {key for key, value in owner.items() if _contains_data_reference(value, data)}


def _encoded_reference_keys(value):
    if isinstance(value, dict):
        if set(value) == {'$id'}:
            return {value['$id']}
        return set().union(*(_encoded_reference_keys(entry) for entry in value.values()))
    if isinstance(value, list):
        return set().union(*(_encoded_reference_keys(entry) for entry in value))
    return set()


def single_armature_user(rig):
    """Allow only one actual data binding plus proven Worklist recovery refs.

    Blender counts ID-property references and fake users in Armature.users.
    Extra references are accepted only for this managed rig's exact session
    or local Custom recovery records; foreign owners and real shared data
    retain the ordinary single-user rejection. No packet I/O is needed.
    Removed-row drafts and their local Action copies remain recovery owners
    when exact native rig/data references and the full Rest proof still match;
    row membership or a plaintext owner label is not an ownership proof.
    """
    if rig.data.users == 1:
        return True
    if not managed(rig):
        return False
    try:
        from . import unity_animation as ua
        data = rig.data
        if rig.library or rig.override_library or data.library:
            return False
        bound = [obj for obj in bpy.data.objects if obj.type == 'ARMATURE' and obj.data is data]
        if len(bound) != 1 or bound[0] is not rig:
            return False
        original, allowed_rig_keys = data, set()
        if is_adopted(rig):
            record = _load(rig)
            artist = record['capture']['targets'][0]
            original = artist['data']
            if data is not original and data is not record['motion_data']:
                return False
            _validated_names(record['mapped_names'])
            _prove_motion_data(original, record['motion_data'], artist['rest'],
                               record['motion_rest'], record['mapped_names'])
            if set(rig.get(REFS, {})) != _encoded_reference_keys(json.loads(rig[SESSION])):
                return False
            allowed_rig_keys.add(REFS)
        if _data_reference_keys(rig, data) - allowed_rig_keys:
            return False
        owners = bpy.data.user_map().get(data, set())
        if rig not in owners:
            return False
        authenticated = is_adopted(rig)
        for owner in owners:
            if owner is rig:
                continue
            if not isinstance(owner, bpy.types.Action):
                return False
            names, motion, changed = _read_action_motion_data(rig, owner, original)
            if data is not original and data is not motion:
                return False
            if _data_reference_keys(owner, data) - {ua.ORIGINAL_DATA_KEY, ua.PREVIEW_DATA_KEY}:
                return False
            authenticated = True
        return authenticated
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def _validate_record(context, rig, record):
    from . import unity_animation as ua
    from . import body_original_mode as original
    saved = context.scene.character_designer_animation_worklist
    if (record['scene'] is not context.scene or record['view_layer'] != context.view_layer.name
            or record['workspace_identity'] != saved.workspace_identity or saved.rig is not rig):
        raise ValueError('Return to the original Main Rig scene, view layer and workspace before continuing.')
    if ua.active_preview(rig):
        raise ValueError('Restore the unrelated Unity preview before using or restoring Main Rig.')
    if original.active(rig):
        raise ValueError('Return to Controls before continuing the adopted Main Rig worklist.')
    _validate_capture(context, record['capture'])
    if rig.data is not record['motion_data'] or not ua._matches_rest_state(rig.data, record['motion_rest']):
        raise ValueError('The adopted Main Rig Rest/data changed; its artist recovery snapshot was kept.')
    packet = ua.load_package(record['packet_path'])
    if packet['_sha256'] != record['packet_sha256']:
        raise ValueError('The adopted Main Rig packet changed; recovery left the artist untouched.')
    context.view_layer.update()
    ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
    previous = record['capture']['targets'][0]['action']
    action = rig.animation_data.action if rig.animation_data else None
    allowed = [previous] + [value for item in saved.items for value in (item.source_action, item.custom_action)]
    if action not in allowed:
        raise ValueError('The artist selected an unrelated Action; select a Worklist Action before restoring Main Rig.')


def ensure_active(context, rig):
    if not managed(rig):
        return
    if not is_adopted(rig):
        raise ValueError('Click Use Main Rig before activating or importing more worklist motions.')
    _validate_record(context, rig, _load(rig))


def adopt_main_rig(context):
    from . import unity_animation as ua
    from . import animation_worklist as worklist, body_original_mode as original
    worklist._idle(context)
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Use Main Rig in Object or Pose Mode after finishing the current edit session.')
    saved = worklist.state(context)
    setup = getattr(context.scene, 'character_designer_setup', None)
    rig = getattr(setup, 'rig', None)
    if (rig is None or rig.type != 'ARMATURE' or rig.library or rig.data.library
            or rig.override_library or rig.name not in context.scene.objects
            or context.view_layer.objects.get(rig.name) is not rig):
        raise ValueError('Set one local Main Rig in this scene/view layer before adopting animation.')
    if not saved.workspace_path:
        raise ValueError('Connect the Unity animation workspace before using Main Rig.')
    if saved.rig is not None and saved.rig is not rig:
        raise ValueError('This worklist already belongs to another rig; preserve its existing edits first.')
    if any(item.rig is not rig for item in saved.items):
        raise ValueError('Existing worklist rows belong to another rig; they were not reassigned.')
    if is_adopted(rig):
        ensure_active(context, rig)
        return rig
    if ua.active_preview(rig):
        raise ValueError('Restore the current Unity preview before using Main Rig.')
    if original.active(rig):
        # This strict call precedes leave(). No Rest resync or repair fallback
        # in Original.leave may be authorized by adopting an animation.
        original._validate_session_rest(rig)
    context.view_layer.update()
    prepared = [worklist._prepared(saved, item.clip_key, existing_target=True)
                for item in saved.catalog if item.ready]
    if not prepared:
        raise ValueError('Prepare at least one clip before validating Main Rig adoption.')
    retained = _retained_motion_data(rig, saved.items)
    previous_action_refs = _capture_action_motion_refs(saved.items) if retained else []
    captured = _capture(context, rig)
    previous_rig = saved.rig
    previous_metadata = capture_session_metadata(rig)
    editing_data = None
    try:
        if original.active(rig):
            original.leave(context, rig)
        context.view_layer.update()
        mapped_names = None
        for _record, _clip, _link, packet, _name, _model in prepared:
            mapping = ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
            names = sorted(mapping.indices)
            if mapped_names is not None and names != mapped_names:
                raise ValueError('Prepared clips do not share the same validated Main Rig bone inventory.')
            mapped_names = names
        packet = prepared[0][3]
        _validate_capture(context, captured)
        if retained:
            if frozenset(mapped_names) != retained['mapped_names']:
                raise ValueError('Prepared clips no longer match the retained Custom Action bone inventory.')
            if retained['translated_names']:
                # Start with today's artist display/collections, never a stale
                # data copy. Only the authenticated translation flags differ.
                editing_data = rig.data.copy()
                editing_data.name = rig.data.name + ' · Motion Worklist'
                editing_data.use_fake_user = False
                ua._swap_armature_data(context, rig, editing_data, allow_joint_translation=True,
                                      joint_translation_names=retained['translated_names'])
                _prove_motion_data(captured['targets'][0]['data'], editing_data,
                    captured['targets'][0]['rest'], ua._rest_state(editing_data), mapped_names)
        record = {'version': 1, 'rig': rig, 'scene': context.scene,
                  'view_layer': context.view_layer.name, 'workspace_identity': saved.workspace_identity,
                  'capture': captured, 'packet_path': packet['_path'], 'packet_sha256': packet['_sha256'],
                  'motion_data': rig.data, 'motion_rest': ua._rest_state(rig.data),
                  'mapped_names': mapped_names}
        _store(rig, record)
        if editing_data:
            context.view_layer.update()
            ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
        if retained:
            for item in saved.items:
                attach_action_motion_data(rig, item.custom_action)
        for entry in captured['targets']:
            entry['data'].use_fake_user = True
            if entry['action'] and entry['action'].library is None:
                entry['action'].use_fake_user = True
        rig.pop(RESTORED, None)
        saved.rig = rig
        saved.status, saved.has_error = 'Main Rig connected. Original model, controls and artist recovery snapshot are retained.', False
        return rig
    except Exception:
        _apply_capture(context, rig, captured)
        _restore_action_motion_refs(previous_action_refs)
        restore_session_metadata(rig, previous_metadata)
        saved.rig = previous_rig
        if editing_data is not None and editing_data.users == 0:
            bpy.data.armatures.remove(editing_data)
        raise


def restore_import_preview(context, rig, action):
    """Keep a necessary translation data copy under the adoption transaction."""
    from . import unity_animation as ua
    if not is_adopted(rig):
        return ua.restore_preview(context, rig)
    data = action.get(ua.PREVIEW_DATA_KEY)
    original_fake_user = bool(data.use_fake_user) if data else False
    if data:
        data.use_fake_user = True
    try:
        result = ua.restore_preview(context, rig)
        if data:
            ua._swap_armature_data(context, rig, data)
            record = _load(rig)
            record['motion_data'], record['motion_rest'] = data, ua._rest_state(data)
            _store(rig, record)
        return result
    finally:
        if data:
            data.use_fake_user = original_fake_user


def restore_main_rig(context):
    from . import animation_worklist as worklist
    worklist._idle(context)
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Restore Main Rig in Object or Pose Mode after finishing the current edit session.')
    saved = worklist.state(context)
    rig = saved.rig
    if not is_adopted(rig):
        raise ValueError('This worklist has no adopted Main Rig recovery session.')
    record = _load(rig)
    _validate_record(context, rig, record)
    current = _capture(context, rig)
    metadata = capture_session_metadata(rig)
    action_refs = _capture_action_motion_refs(saved.items)
    try:
        for item in saved.items:
            attach_action_motion_data(rig, item.custom_action)
        _apply_capture(context, rig, record['capture'])
        rig.pop(SESSION, None)
        rig.pop(REFS, None)
        rig[RESTORED] = True
        saved.active_index = -1
        saved.status, saved.has_error = 'Artist Main Rig restored. Local animation drafts are kept; click Use Main Rig to edit them again.', False
        return rig
    except Exception:
        _apply_capture(context, rig, current)
        _restore_action_motion_refs(action_refs)
        restore_session_metadata(rig, metadata)
        raise
