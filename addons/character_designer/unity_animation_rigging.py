"""Explicit Unity TwoBoneIK inputs on the existing Character Designer arms.

The base Action remains an independent animation.  The carrier contains only
Unity-local Target/Hint inputs; the native preview never records solved bones.
This module is source-stage code: installed-binary and round-trip acceptance
are required before deployment.
"""

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import uuid

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import animation_composition as composition
from . import animation_rigging_contract as contract
from . import animation_worklist_fingerprint as fingerprints
from . import bone_collections, bone_display, limb_ik, limb_ik_fk, unity_animation as ua


SESSION_KEY = 'character_designer_rigging_session_v1'
CAPTURE_KEY = 'character_designer_rigging_capture_v1'
_REFS_KEY = 'character_designer_rigging_refs_v1'
_INPUT_REF = 'character_designer_rigging_input_action_v1'
_INPUT_META = 'character_designer_rigging_input_owner_v1'
_INPUT_BASE = 'character_designer_rigging_base_action_v1'
_PREVIEW_REF_META = 'character_designer_rigging_preview_reference_v1'
_SIDES = ('Right', 'Left')
_FIELDS = (('targetPosition', 3), ('targetRotation', 4), ('hintPosition', 3))
_CACHE = {}
_BUSY = set()
_SUSPENDED = {}
_HANDLER_ERRORS = {}
_OWNED_TOKENS = {}
_REACQUIRE_PENDING = {}
# Retain the owned timer handle/generation across an in-place module reload so
# register/unregister can cancel the previous callback before admitting IDs.
_REACQUIRE_TIMER = globals().get('_REACQUIRE_TIMER')
_REACQUIRE_GENERATION = globals().get('_REACQUIRE_GENERATION', 0)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _uid(value):
    result = uuid.UUID(str(value))
    _require(result.int != 0, 'Rigging needs an actual nonzero work-item identity.')
    return result.hex


def _property(side, field):
    return 'cd_rigging_' + side.lower() + '_' + field


def _channels():
    return {('[' + json.dumps(_property(side, field)) + ']', index)
            for side in _SIDES for field, count in _FIELDS for index in range(count)}


def _curves(action, slot):
    _require(len(action.slots) == 1 and action.slots[0].handle == slot
             and len(action.layers) == 1 and len(action.layers[0].strips) == 1,
             'An input carrier has exactly one native Rig slot and keyframe layer.')
    bag = composition._bag(action, slot)
    _require(bag is not None, 'The exact Rigging input Action slot is missing.')
    curves = list(bag.fcurves)
    keys = [(curve.data_path, curve.array_index) for curve in curves]
    _require(len(keys) == len(set(keys)) and set(keys) == _channels(),
             'Rigging input Actions contain only the four Target/Hint local inputs.')
    for curve in curves:
        _require(not curve.mute and not curve.lock and not curve.modifiers
                 and not curve.sampled_points and len(curve.keyframe_points) > 0
                 and not getattr(curve, 'is_driver', False)
                 and getattr(curve, 'driver', None) is None,
                 'Rigging inputs need explicit unmodified finite keyframe curves.')
        _require(all(math.isfinite(float(v)) for point in curve.keyframe_points
                     for vector in (point.co, point.handle_left, point.handle_right)
                     for v in vector), 'A Rigging input key is non-finite.')
    return {(curve.data_path, curve.array_index): curve for curve in curves}


def _motion(action, slot):
    proof = fingerprints.fingerprint_action(action, slot)
    _require(proof.get('fingerprint_known') is True, 'The selected Action slot cannot be authenticated.')
    return proof['action_fingerprint']


def _detached(value):
    references = {}
    return composition._decode(composition._encode(value, references), references)


def _input_record(base_action, base_slot, work_item_id):
    _require(type(base_slot) is int and 0 <= base_slot <= 2147483647,
             'Rigging proof needs the exact base slot.')
    work_id = _uid(work_item_id)
    _require((CAPTURE_KEY in base_action) == (_INPUT_REF in base_action),
             'The Custom input capture and carrier reference must be present together.')
    read_only = CAPTURE_KEY not in base_action
    if read_only:
        candidates = [(action, json.loads(action[_INPUT_META])) for action in bpy.data.actions
                      if action.get(_INPUT_BASE) is base_action and _INPUT_META in action]
        candidates = [(action, row) for action, row in candidates
                      if row.get('source_read_only') is True and row.get('base_slot') == base_slot]
        _require(len(candidates) == 1, 'The exact original Source input carrier is missing or ambiguous.')
        action, row = candidates[0]
        capture_sha = row['capture_sha256']
    else:
        _require(CAPTURE_KEY in base_action and _INPUT_REF in base_action,
                 'This base Action has no authenticated Rigging input draft.')
        capture = contract.validate_capture(json.loads(base_action[CAPTURE_KEY]))
        action = base_action[_INPUT_REF]
        _require(isinstance(action, bpy.types.Action), 'The saved Rigging input Action is missing.')
        row, capture_sha = json.loads(action[_INPUT_META]), contract.capture_digest(capture)
        _require(row['new_action_id'] == capture['newActionId'], 'The input draft UUID changed.')
    _require(not action.library and action.get(_INPUT_BASE) is base_action
             and composition._slot(base_action, base_slot) is not None
             and composition._slot(action, row['input_slot']) is not None
             and row['schema'] == 'cdesigner.native-rigging-input-owner/1'
             and row['base_slot'] == base_slot and row['work_item_id'] == work_id
             and row['source_read_only'] is read_only and row['capture_sha256'] == capture_sha,
             'The input Action owner, base slot or original capture changed.')
    _require(base_action.get(ua.PACKAGE_HASH_KEY) == row['source_packet_sha256'],
             'The input carrier is not bound to this actual base source packet.')
    for field in ('source_packet_sha256', 'capture_sha256', 'initial_input_motion'):
        _require(isinstance(row[field], str) and len(row[field]) == 64
                 and all(c in '0123456789abcdef' for c in row[field]),
                 'An original Rigging input provenance digest is malformed.')
    _require(_uid(row['new_action_id']) == row['new_action_id'], 'The input UUID must retain its exact N format.')
    motion = _motion(action, row['input_slot'])
    _curves(action, row['input_slot'])
    if read_only:
        _require(motion == row['source_input_motion'] == row['initial_input_motion'],
                 'The original read-only Source input carrier changed.')
    return action, row, motion


def input_proof(rig, base_action, base_slot, work_item_id, *, source=False):
    """Fixed twenty-channel ownership proof; never certifies a base Action."""
    _require(type(source) is bool, 'Choose the immutable initial or current input motion explicitly.')
    action, row, motion = _input_record(base_action, base_slot, work_item_id)
    _require(row['rig_name'] == rig.name and row['rig_rest_sha256'] == _digest(ua._rest_state(rig.data)),
             'The input proof belongs to another original Rig or Rest skeleton.')
    return {'schema': 'cdesigner.rigging-input-proof/1',
            **{key: row[key] for key in ('source_packet_sha256', 'work_item_id', 'new_action_id',
               'capture_sha256', 'source_read_only', 'initial_input_motion')},
            'base_action': base_action.name, 'base_slot': base_slot,
            'input_action': action.name, 'input_slot': row['input_slot'],
            'input_motion_sha256': row['initial_input_motion'] if source else motion}


def capture_for_action(base_action, base_slot, work_item_id):
    """Read the authenticated original Custom capture without a live session."""
    _input_record(base_action, base_slot, work_item_id)
    _require(CAPTURE_KEY in base_action, 'Use the exact Custom base for this capture proof.')
    return contract.validate_capture(json.loads(base_action[CAPTURE_KEY]))


def _store(rig, session):
    # Native references stay in an owned IDProperty table; never trust names to
    # recover an Action after save/reopen.  The encoder is the existing strict
    # Composition native-ID serializer, not a script stored in the blend.
    refs = {}
    state = {key: value for key, value in session.items() if not key.startswith('_')}
    encoded = composition._encode(state, refs)
    payload = json.dumps({'payload': encoded, 'sha256': _digest(encoded)},
        sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    had_refs, had_state = _REFS_KEY in rig, SESSION_KEY in rig
    old_refs, old_state = dict(rig.get(_REFS_KEY, {})), rig.get(SESSION_KEY)
    try:
        rig[_REFS_KEY], rig[SESSION_KEY] = refs, payload
    except Exception:
        if had_refs:
            rig[_REFS_KEY] = old_refs
        else:
            rig.pop(_REFS_KEY, None)
        if had_state:
            rig[SESSION_KEY] = old_state
        else:
            rig.pop(SESSION_KEY, None)
        raise
    _CACHE[rig.as_pointer()] = session


def current(rig):
    """Rebuild the owned session from persistent native IDs, or return None."""
    if rig is None or SESSION_KEY not in rig:
        return None
    envelope = json.loads(rig[SESSION_KEY])
    _require(set(envelope) == {'payload', 'sha256'}
             and _digest(envelope['payload']) == envelope['sha256'],
             'The saved Rigging session is incomplete or changed.')
    used = set()
    session = composition._decode(envelope['payload'], rig.get(_REFS_KEY, {}), used)
    _require(used == set(rig.get(_REFS_KEY, {})) and session.get('version') == 1
             and session.get('rig') is rig, 'This Rigging session belongs to another native Rig.')
    cached = _CACHE.get(rig.as_pointer())
    if cached and cached.get('owner') == session['owner'] and cached.get('rig') is rig:
        session.update({key: value for key, value in cached.items() if key.startswith('_')})
    _validate(session)
    _CACHE[rig.as_pointer()] = session
    return session


def display_state(rig):
    """Cheap UI-only view of an already authenticated in-process session."""
    if rig is None or SESSION_KEY not in rig:
        return None
    if rig.as_pointer() in _REACQUIRE_PENDING or rig.as_pointer() in _HANDLER_ERRORS:
        return None
    session = _CACHE.get(rig.as_pointer())
    if not session or session.get('rig') is not rig:
        return None
    return {**{key: session[key] for key in ('rig', 'scene', 'base_action', 'input_action', 'edit_role',
            'source_read_only', 'enabled', 'tweak_enabled')},
            'original_controls_only': 'original_inputs' in session}


def _validate(session):
    rig, action = session['rig'], session['input_action']
    _require(rig.type == 'ARMATURE' and not rig.library and not rig.data.library
             and rig.data.name == session['data_name']
             and _digest(ua._rest_state(rig.data)) == session['rest_sha256'],
             'Rigging preview cannot replace the original Rig or Rest skeleton.')
    _require([float(v) for row in rig.matrix_world for v in row] == session['object_matrix'],
             'Rigging preview needs the authenticated character object transform.')
    _require(session['base_action'] is not action
             and composition._slot(session['base_action'], session['base_slot']) is not None
             and composition._slot(action, session['input_slot']) is not None
             and action.get(_INPUT_BASE) is session['base_action'],
             'The base and input Actions or their exact slots changed.')
    meta = json.loads(action[_INPUT_META])
    _require(meta == session['input_owner'], 'The Rigging input owner record changed.')
    _require(contract.capture_digest(session['capture']) == meta['capture_sha256'],
             'The original Rigging capture changed.')
    _require(meta['new_action_id'] == session['capture']['newActionId'] == session['new_action_id']
             and meta['source_packet_sha256'] == session['source_packet_sha256']
             and session['base_action'].get(ua.PACKAGE_HASH_KEY) == session['source_packet_sha256']
             and meta['work_item_id'] == session['work_item_id']
             and meta['source_read_only'] is session['source_read_only'],
             'The captured draft, packet or WorkItem identity changed.')
    _curves(action, session['input_slot'])
    if session['source_read_only']:
        _require(_motion(action, session['input_slot']) == meta['initial_input_motion'] == meta['source_input_motion'],
                 'Read-only Source input curves cannot be edited.')
    if 'original_inputs' in session:
        from . import unity_animation_original_inputs
        unity_animation_original_inputs.validate_session(session)
    _inventory(session)
    _validate_preview_reference(session)
    if session['tracks']:
        _require([session['scene'].render.fps, session['scene'].render.fps_base] == session['scene_clock'],
                 'Restore the owning Scene clock before editing this Rigging preview.')
        if not _SUSPENDED.get(rig.as_pointer(), 0):
            tracks, ad = _tracks(session), rig.animation_data
            owned = {track.as_pointer() for track in tracks.values()}
            _require(ad is not None and ad.use_nla
                     and all(not track.mute and not track.is_solo for track in tracks.values())
                     and all(track.mute and not track.is_solo for track in ad.nla_tracks
                             if track.as_pointer() not in owned)
                     and ((session['tweak_enabled'] and ad.use_tweak_mode
                           and ad.action is (session['base_action'] if session['edit_role'] == 'BASE' else action)
                           and ad.action_slot_handle == (session['base_slot'] if session['edit_role'] == 'BASE' else session['input_slot']))
                          or (not session['tweak_enabled'] and not ad.use_tweak_mode and ad.action is None)),
                     'The exact owned BASE/Input stack or native Tweak binding changed.')


def _inventory(session):
    inventory = limb_ik._validate_inventory(session['rig'])
    for row in session['native_bindings']:
        entry = inventory['rigs'].get(('ARM', row['native_side']))
        _require(entry is not None and entry['rig_id'] == row['rig_id']
                 and list(entry['chain']) == row['chain']
                 and entry['target'].name == row['target']
                 and entry['pole'].name == row['pole']
                 and entry['solver_target'].name == row['target']
                 and entry['auto_align'] == row['auto_align']
                 and entry['auto_rotation_space'] == row['auto_rotation_space'],
                 'The original arm Target/Pole control graph changed.')
    return inventory


def _matrix(values):
    _require(isinstance(values, (list, tuple)) and len(values) == 16
             and all(type(v) in (int, float) and math.isfinite(v) for v in values),
             'Rigging parent matrices must contain sixteen finite row-major values.')
    result = Matrix([values[index:index + 4] for index in range(0, 16, 4)])
    _require(abs(result.determinant()) > 1e-12, 'A Rigging parent frame is singular.')
    return result


def _rows(matrix):
    return [float(v) for row in matrix for v in row]


def _binding_plan(rig, packet, capture, mapping):
    by_path = {bone['path']: (bone['name'], bone) for bone in packet['bones']}
    inventory = limb_ik._validate_inventory(rig)
    plan = []
    for index, binding in enumerate(capture['bindings']):
        side = ('R', 'L')[index]
        entry = inventory['rigs'].get(('ARM', side))
        names = [by_path[binding[field]][0] for field in ('rootPath', 'midPath', 'tipPath')]
        _require(entry is not None and list(entry['chain']) == names,
                 'Unity TwoBoneIK must bind the exact original native arm chain.')
        _require(entry['solver_target'] is entry['target']
                 or entry['solver_target'].name == entry['target'].name,
                 'This native arm has an unsupported separate solver Target.')
        for field in ('rigWeight', 'constraintWeight', 'targetPositionWeight',
                      'targetRotationWeight', 'hintWeight'):
            _require(binding[field] in (0., 1.),
                     'Partial Rigging weights need explicit calibrated proof before editing.')
        _require(not binding['maintainTargetPositionOffset']
                 and not binding['maintainTargetRotationOffset'],
                 'Maintained Target offsets need explicit offset proof before editing.')
        end_constraint = next(con for _pb, con, record in entry['entries']
                              if record['role'] == 'END_ROTATION')
        _require(entry['auto_align'] or (end_constraint.owner_space == 'WORLD'
                 and end_constraint.target_space == 'WORLD'),
                 'This wrist orientation mode has no explicit Target input mapping.')
        _require(not entry['auto_align'] or entry['auto_rotation_space'] in {'LOCAL', 'PARENT_DELTA'},
                 'This Auto Align wrist mode is unsupported.')
        plan.append({'native_side': side, 'rig_id': entry['rig_id'], 'chain': names,
                     'target': entry['target'].name, 'pole': entry['pole'].name,
                     'auto_align': entry['auto_align'],
                     'auto_rotation_space': entry['auto_rotation_space'],
                     'unity_tip_rest': by_path[binding['tipPath']][1]['rest'],
                     'native_tip_rest': _rows(mapping.rest_world[names[2]])})
    # Current contract intentionally supports the actual Rig1 parents and the
    # Left Target parented to RightHand.  Back edges and self-owned parents are
    # rejected rather than solved or guessed from the output skeleton.
    right, left = capture['bindings']
    for index, binding in enumerate(capture['bindings']):
        for field in ('targetParentPath', 'hintParentPath'):
            path = binding[field]
            if path in by_path:
                _require(index == 1 and field == 'targetParentPath'
                         and path == right['tipPath'],
                         'Only the declared Left Target -> RightHand dynamic parent is supported.')
            _require(path not in {binding['targetPath'], binding['hintPath'],
                                 right['targetPath'], right['hintPath'],
                                 left['targetPath'], left['hintPath']},
                     'Target/Hint ownership would form a Rigging input dependency cycle.')
    return plan


def _new_input_action(rig, capture, base_action, base_slot, source, packet_sha, work_id,
                      *, source_action_id=None):
    action_id = capture['newActionId']
    if source_action_id is not None:
        _require(source is True, 'A separate Source identity is only for a new read-only input carrier.')
        action_id = _uid(source_action_id)
        _require(action_id != capture['newActionId'],
                 'The read-only Source and editable captured input draft need distinct identities.')
    action = bpy.data.actions.new('Rigging Inputs · ' + ('Source' if source else 'Custom'))
    try:
        slot, bag = ua._new_channelbag(action, rig)
        rate = capture['sampleRate']
        frames = [1. + frame['time'] * rate for frame in capture['frames']]
        rotations = []
        for index in range(2):
            continuous, previous = [], None
            for frame in capture['frames']:
                values = list(frame['controls'][index]['targetRotation'])
                if previous is not None and sum(a * b for a, b in zip(previous, values)) < 0:
                    values = [-v for v in values]
                continuous.append(values)
                previous = values
            rotations.append(continuous)
        for index, side in enumerate(_SIDES):
            for field, count in _FIELDS:
                path = '[' + json.dumps(_property(side, field)) + ']'
                for component in range(count):
                    values = ([row[component] for row in rotations[index]] if field == 'targetRotation'
                              else [frame['controls'][index][field][component] for frame in capture['frames']])
                    ua._write_curve(bag, path, component, frames, values)
        owner = {'schema': 'cdesigner.native-rigging-input-owner/1',
                 'capture_sha256': contract.capture_digest(capture),
                 'source_packet_sha256': packet_sha, 'work_item_id': work_id,
                 'base_slot': base_slot, 'input_slot': slot.handle,
                 'new_action_id': action_id, 'source_read_only': source,
                 'rig_name': rig.name, 'rig_rest_sha256': _digest(ua._rest_state(rig.data)),
                 'initial_input_motion': _motion(action, slot.handle)}
        if source:
            owner['source_input_motion'] = owner['initial_input_motion']
        action[_INPUT_META] = json.dumps(owner, sort_keys=True, separators=(',', ':'), allow_nan=False)
        action[_INPUT_BASE] = base_action
        action.use_fake_user = True
        _curves(action, slot.handle)
        return action, slot.handle, owner
    except Exception:
        bpy.data.actions.remove(action)
        raise


def import_inputs(context, rig, packet, *, base_action, base_slot, work_item_id='', source=False):
    """Authenticate inputs after ordinary base import; create no solved Action."""
    if 'rigging' not in packet:
        return None
    _require(current(rig) is None, 'Close the current Rigging preview before importing another base.')
    _require(type(source) is bool and rig.type == 'ARMATURE' and not rig.library
             and not rig.data.library and not composition.active(rig),
             'Rigging needs the local original Rig outside another Composition session.')
    capture = contract.validate_capture(packet['rigging'], packet=packet)
    work_id = _uid(work_item_id)
    _require(work_id != capture['newActionId'], 'The captured input draft and Unity WorkID must be independent.')
    packet_sha = packet.get('_sha256')
    _require(isinstance(packet_sha, str) and len(packet_sha) == 64
             and all(c in '0123456789abcdef' for c in packet_sha),
             'Rigging inputs need the authenticated complete source packet SHA256.')
    _require(composition._slot(base_action, base_slot) is not None,
             'Rigging needs the actual selected base Action slot.')
    _require(base_action.get(ua.PACKAGE_HASH_KEY) == packet_sha,
             'Import Rigging only on the actual prepared base packet Action.')
    _require(ua.native_action_frame_rate(base_action) == capture['sampleRate'],
             'Import the exact prepared base on its native packet clock before adding Rigging inputs.')
    source = source or bool(base_action.library)
    mapping = ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
    bindings = _binding_plan(rig, packet, capture, mapping)
    prior, display = composition._prior(context, rig), bone_display._snapshot(rig)
    input_action = None
    session = None
    created = False
    had_capture, old_capture = CAPTURE_KEY in base_action, base_action.get(CAPTURE_KEY)
    had_ref, old_ref = _INPUT_REF in base_action, base_action.get(_INPUT_REF)
    _require(source or had_capture == had_ref, 'The existing Custom capture or carrier reference is incomplete.')
    try:
        if not source and had_ref:
            input_action = old_ref
            _require(isinstance(input_action, bpy.types.Action) and not input_action.library,
                     'The saved Custom Rigging input Action is missing.')
            owner = json.loads(input_action[_INPUT_META])
            _require(input_action.get(_INPUT_BASE) is base_action
                     and owner['base_slot'] == base_slot
                     and owner['work_item_id'] == work_id
                     and owner['source_packet_sha256'] == packet_sha
                     and owner['capture_sha256'] == contract.capture_digest(capture)
                     and owner['source_read_only'] is False,
                     'The existing Custom input draft belongs to another base or source capture.')
            input_slot = owner['input_slot']
            _curves(input_action, input_slot)
        elif source:
            candidates = []
            for action in bpy.data.actions:
                if action.get(_INPUT_BASE) is not base_action or _INPUT_META not in action:
                    continue
                record = json.loads(action[_INPUT_META])
                if record.get('source_read_only') is True and record.get('base_slot') == base_slot:
                    _require(record.get('work_item_id') == work_id
                             and record.get('source_packet_sha256') == packet_sha
                             and record.get('capture_sha256') == contract.capture_digest(capture),
                             'An existing Source input carrier has inconsistent provenance.')
                    candidates.append((action, record))
            _require(len(candidates) <= 1, 'Several owned Source input carriers bind this base slot.')
            if candidates:
                input_action, owner = candidates[0]
                input_slot = owner['input_slot']
                _curves(input_action, input_slot)
                _require(_motion(input_action, input_slot) == owner['source_input_motion'],
                         'The original read-only Source input carrier changed.')
            else:
                input_action, input_slot, owner = _new_input_action(
                    rig, capture, base_action, base_slot, source, packet_sha, work_id)
                created = True
        else:
            input_action, input_slot, owner = _new_input_action(
                rig, capture, base_action, base_slot, source, packet_sha, work_id)
            created = True
        session = {'version': 1, 'owner': str(uuid.uuid4()), 'rig': rig,
                   'scene': context.scene, 'capture': capture,
                   'base_action': base_action, 'base_slot': base_slot,
                   'input_action': input_action, 'input_slot': input_slot,
                   'new_action_id': owner['new_action_id'], 'work_item_id': work_id,
                   'source_packet_sha256': packet_sha, 'source_read_only': source,
                   'input_owner': owner, 'native_bindings': bindings,
                   'conversion': _rows(mapping.conversion), 'data_name': rig.data.name,
                   'rest_sha256': _digest(ua._rest_state(rig.data)),
                   'object_matrix': _rows(rig.matrix_world), 'prior': prior,
                   'display': display, 'tracks': {}, 'enabled': False,
                   'edit_role': 'BASE', 'tweak_enabled': False,
                   'properties_before': {}, 'committed': True}
        if not source:
            base_action[CAPTURE_KEY] = json.dumps(capture, sort_keys=True,
                ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            base_action[_INPUT_REF] = input_action
        _store(rig, session)
        if capture.get('weaponPreview') is not None:
            from . import animation_composition_weapon as weapon
            session['weapon_preview'] = weapon.add_rigging_context(
                context, rig, capture['weaponPreview'], packet, mapping, session=session)
            _store(rig, session)
        if capture['previewEnabled']:
            activate(context, session)
        return session
    except Exception as primary:
        cleanup_errors = []
        if session is not None and 'weapon_preview' in session:
            try:
                # Keep the successful local proof even if its second _store
                # failed and restored the first, pre-display session envelope.
                from . import animation_composition_weapon as weapon
                if weapon.RECORD in rig:
                    weapon.remove_rigging_context(context, rig, session=session)
            except Exception as error:
                cleanup_errors.append('new weapon display: ' + str(error))
        if SESSION_KEY in rig:
            try:
                restore_owned(context, rig)
            except Exception as error:
                cleanup_errors.append(str(error))
        if not source:
            for key, present, value in ((CAPTURE_KEY, had_capture, old_capture), (_INPUT_REF, had_ref, old_ref)):
                try:
                    if present:
                        base_action[key] = value
                    else:
                        base_action.pop(key, None)
                except Exception as error:
                    cleanup_errors.append('original ' + key + ': ' + str(error))
        try:
            if created and input_action is not None and input_action.users == int(input_action.use_fake_user):
                input_action.use_fake_user = False
                bpy.data.actions.remove(input_action)
        except Exception as error:
            cleanup_errors.append('new input carrier: ' + str(error))
        try:
            composition._restore_prior(context, rig, prior)
        except Exception as error:
            cleanup_errors.append(str(error))
        try:
            bone_display._restore(rig, display)
        except Exception as error:
            cleanup_errors.append(str(error))
        if cleanup_errors:
            primary.add_note('Owned Rigging import recovery: ' + '; '.join(cleanup_errors))
        raise


def _tracks(session):
    ad = session['rig'].animation_data
    result = {}
    for role, row in session['tracks'].items():
        track = ad.nla_tracks.get(row['name']) if ad else None
        _require(track is not None and len(track.strips) == 1
                 and track.strips[0].action is row['action']
                 and track.strips[0].action_slot_handle == row['slot'],
                 'An owned Rigging preview track or Action slot changed.')
        _require(all(composition._plain(getattr(track.strips[0], key)) == value
                     for key, value in row['fields'].items()),
                 'Owned Rigging preview timing or blending changed.')
        result[role] = track
    if result:
        reference = session.get('preview_reference')
        action, slot = ((reference['action'], reference['slot']) if reference
                        else (session['base_action'], session['base_slot']))
        _require(session['tracks']['BASE']['action'] is action
                 and session['tracks']['BASE']['slot'] == slot
                 and session['tracks']['RIGGING']['action'] is session['input_action']
                 and session['tracks']['RIGGING']['slot'] == session['input_slot'],
                 'The preview reference or original input track owner changed.')
    return result


def _reference_file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _preview_reference_seal(session, reference):
    return {'schema': 'cdesigner.rigging-preview-reference/1',
        'packet_sha256': reference['packet_sha256'], 'model_sha256': reference['model_sha256'],
        'motion_sha256': reference['motion_sha256'], 'slot': reference['slot'],
        'rig_name': session['rig'].name, 'formal_source_packet_sha256': session['source_packet_sha256'],
        'work_item_id': session['work_item_id']}


def _validate_preview_reference(session, *, files=False, sealed=True):
    if 'original_inputs' in session:
        from . import unity_animation_original_inputs
        return unity_animation_original_inputs.validate_reference(session, files=files, sealed=sealed)
    reference = session.get('preview_reference')
    if reference is None:
        return
    _require(isinstance(reference, dict) and set(reference) == {
        'action', 'slot', 'packet_file', 'packet_sha256', 'model_file', 'model_sha256',
        'sample_rate', 'duration', 'motion_sha256', 'mapping_sha256', 'root_path'},
        'The read-only preview reference record is incomplete.')
    action, rig = reference['action'], session['rig']
    _require(isinstance(action, bpy.types.Action) and not action.library
             and action is not session['base_action'] and action is not session['input_action']
             and composition._slot(action, reference['slot']) is not None
             and action.get(ua.TARGET_KEY) is rig and action.get(ua.SCENE_KEY) is session['scene']
             and action.get(ua.VERSION_KEY) == 2
             and not any(key in action for key in (CAPTURE_KEY, _INPUT_REF, _INPUT_META, _INPUT_BASE,
                                                  ua.ORIGINAL_DATA_KEY, ua.PREVIEW_DATA_KEY))
             and action.get(ua.PACKAGE_HASH_KEY) == reference['packet_sha256']
             and str(Path(action.get(ua.PACKAGE_KEY, '')).resolve()) == reference['packet_file']
             and action.get(ua.NATIVE_TIMEBASE_KEY) == reference['sample_rate']
             and action.get('unity_sample_rate') == reference['sample_rate']
             and action.get('unity_duration') == reference['duration']
             and action.get('unity_start_frame') == 1.
             and _motion(action, reference['slot']) == reference['motion_sha256'],
             'The read-only reference Action, native slot, source or curves changed.')
    if sealed:
        _require(json.loads(action.get(_PREVIEW_REF_META, 'null')) == _preview_reference_seal(session, reference),
                 'The reference must retain its original public-import ownership and curve proof.')
    _require(type(reference['duration']) in (int, float) and math.isfinite(reference['duration'])
             and reference['duration'] > 0 and type(reference['sample_rate']) in (int, float)
             and math.isfinite(reference['sample_rate']) and 1 <= reference['sample_rate'] <= 240
             and reference['duration'] * reference['sample_rate'] >= 1.
             and rig.get('character_designer_animation_source_model_sha256') == reference['model_sha256']
             and not (session['edit_role'] == 'BASE' and session['tweak_enabled']),
             'The reference clock/model changed, or its read-only BASE was opened for editing.')
    if files:
        _require(_reference_file_sha(reference['model_file']) == reference['model_sha256']
                 and _reference_file_sha(reference['packet_file']) == reference['packet_sha256']
                 and ua.native_action_frame_rate(action) == reference['sample_rate'],
                 'A saved preview reference input changed; its native draft was retained.')


def _reference_model_identity(session, model_file, model_sha256):
    from . import animation_link as links
    model_path = Path(model_file).expanduser().resolve()
    _require(Path(model_file).is_absolute()
             and model_path.suffix.lower() == '.fbx' and model_path.is_file()
             and isinstance(model_sha256, str) and len(model_sha256) == 64
             and all(c in '0123456789abcdef' for c in model_sha256)
             and _reference_file_sha(model_path) == model_sha256,
             'Use the exact actual reference packet and this formal Worklist model.')
    rig, association = session['rig'], links.get_link(session['rig'])
    _require(association is not None
             and Path(association['model_file']).resolve() == model_path
             and association['model_sha256'] == model_sha256
             and association['identity']['sourcePackageSha256'].lower() == session['source_packet_sha256']
             and association['identity']['targetGuid'].lower() == session['capture']['targetGuid'],
             'The reference model must belong to the unchanged formal base association.')
    return model_path


def _reference_admission(context, session, action, slot, packet_file, packet_sha256,
                         model_file, model_sha256, *, _creating=False):
    _require(type(slot) is int and slot >= 0 and composition._slot(action, slot) is not None,
             'Select the actual reference Action and its exact native slot.')
    model_path = _reference_model_identity(session, model_file, model_sha256)
    packet_path = Path(packet_file).expanduser().resolve()
    _require(Path(packet_file).is_absolute() and isinstance(packet_sha256, str)
             and len(packet_sha256) == 64 and all(c in '0123456789abcdef' for c in packet_sha256),
             'Use the actual absolute reference packet and its complete SHA256.')
    rig = session['rig']
    packet = ua.load_package(packet_path)
    _require(packet['_sha256'] == packet_sha256
             and ua.native_action_frame_rate(action) == packet['sampleRate'],
             'The reference native Action clock does not match its actual complete packet.')
    roots = [bone for bone in packet['bones'] if bone['parent'] < 0]
    root = roots[0] if len(roots) == 1 else None
    root_path = session['capture']['bindings'][0]['rootPath'].split('/')[0]
    _require(root is not None and root['name'] == root['path'] == root_path
             and root['restSource'] == 'hierarchy' and root.get('weighted') is False
             and len({bone['name'] for bone in packet['bones']}) == len(packet['bones'])
             and len({bone['path'] for bone in packet['bones']}) == len(packet['bones']),
             'The reference needs the same explicit source root and unique complete hierarchy.')
    mapping = ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
    _require(mapping.controls is not None
             and set(mapping.indices) == set(rig.data.bones.keys()) - set(mapping.controls.control_names)
             and _rows(mapping.conversion) == session['conversion'],
             'The actual reference does not match the complete original Rest and ControlPlan.')
    plan = _binding_plan(rig, packet, session['capture'], mapping)
    _require(plan == session['native_bindings'],
             'The reference must preserve the exact original native two-arm binding plan.')
    reference = {'action': action, 'slot': slot, 'packet_file': str(packet_path),
        'packet_sha256': packet_sha256, 'model_file': str(model_path), 'model_sha256': model_sha256,
        'sample_rate': packet['sampleRate'], 'duration': packet['duration'],
        'motion_sha256': _motion(action, slot), 'root_path': root_path,
        'mapping_sha256': _digest({'indices': mapping.indices, 'conversion': _rows(mapping.conversion),
            'rest_world': {name: _rows(value) for name, value in mapping.rest_world.items()}})}
    checked = dict(session, preview_reference=reference)
    _validate_preview_reference(checked, files=True, sealed=not _creating)
    if _creating:
        _require(_PREVIEW_REF_META not in action, 'Do not reseed an existing read-only reference proof.')
        action[_PREVIEW_REF_META] = json.dumps(_preview_reference_seal(session, reference),
            sort_keys=True, separators=(',', ':'), allow_nan=False)
    return reference


def _preview_base_timing(session, reference=None):
    if 'original_inputs' in session:
        from . import unity_animation_original_inputs
        return unity_animation_original_inputs.preview_base_timing(session, reference)
    duration, scene_rate = session['capture']['duration'], session['scene_clock'][0] / session['scene_clock'][1]
    action, slot = ((reference['action'], reference['slot']) if reference
                    else (session['base_action'], session['base_slot']))
    rate = reference['sample_rate'] if reference else session['capture']['sampleRate']
    source_duration = reference['duration'] if reference else duration
    _require(duration > 0 and source_duration > 0 and scene_rate > 0,
             'Reference preview needs positive source and destination clocks.')
    return action, slot, {'begin': 1., 'end': 1. + source_duration * rate,
        'scene_start': session['scene_start'], 'scene_end': session['scene_start'] + duration * scene_rate,
        'scale': duration * scene_rate / (source_duration * rate)}


def _assign_preview_base(strip, action, slot, timing):
    strip.action = action
    strip.action_slot = composition._slot(action, slot)
    strip.action_frame_start, strip.action_frame_end = timing['begin'], timing['end']
    strip.repeat = 1.
    strip.scale = timing['scale']
    strip.frame_start, strip.frame_end = timing['scene_start'], timing['scene_end']
    strip.blend_type, strip.influence, strip.extrapolation = 'REPLACE', 1., 'HOLD'
    strip.blend_in = strip.blend_out = 0.
    strip.use_auto_blend = strip.use_animated_influence = strip.use_animated_time = False
    strip.use_animated_time_cyclic = strip.use_reverse = strip.use_sync_length = False


def prepare_preview_reference(context, session, reference_packet_file, reference_packet_sha256, *,
                              model_file, model_sha256):
    """Return (Action, slot) from one restored native-clock reference import.

    Only the base animation is imported. Complete reference packet bytes stay
    unchanged; its Rigging/weapon inputs never acquire a session or carrier.
    This does not select the reference: use set_preview_reference afterwards.
    """
    _validate(session)
    rig = session['rig']
    _require(context.scene is session['scene'] and session['tracks'] and session['enabled']
             and not session['source_read_only'] and session['edit_role'] == 'BASE'
             and not session['tweak_enabled'] and not rig.animation_data.use_tweak_mode
             and not _SUSPENDED.get(rig.as_pointer(), 0) and rig.as_pointer() not in _BUSY
             and ua.active_preview(rig) is None,
             'Prepare a reference on its enabled Custom BASE owner outside Tweak/export.')
    _reference_model_identity(session, model_file, model_sha256)
    packet = ua.load_package(reference_packet_file)
    _require(packet['_sha256'] == reference_packet_sha256,
             'The actual complete reference packet differs from its supplied SHA256.')
    action, slot = None, None
    original_data, original_rest = rig.data, ua._rest_state(rig.data)
    actions_before, data_before = set(bpy.data.actions), set(bpy.data.armatures)
    base_motion = _motion(session['base_action'], session['base_slot'])
    input_motion = _motion(session['input_action'], session['input_slot'])
    try:
        with base_only(context, rig, action=session['base_action']):
            prior = composition._prior(context, rig)
            try:
                result = ua._import_package_action(context, rig, packet, start_frame=1., native_timebase=True)
                action = result.action
                _require(action not in actions_before and action.get(ua.TARGET_KEY) is rig,
                         'The native reference import returned a foreign Action.')
                slot = rig.animation_data.action_slot_handle
                ua.restore_preview(context, rig)
                _require(rig.data is original_data and ua._rest_state(rig.data) == original_rest
                         and ua.active_preview(rig) is None,
                         'Restore the complete temporary native reference preview before accepting it.')
            except Exception as primary:
                # The known returned Action alone authenticates a temporary
                # data copy. Never sweep new IDs or force a foreign replacement.
                errors = []
                try:
                    preview = action.get(ua.PREVIEW_DATA_KEY) if action is not None and action not in actions_before else None
                    if rig.data is not original_data:
                        _require(preview is rig.data and preview not in data_before
                                 and action.get(ua.ORIGINAL_DATA_KEY) is original_data
                                 and ua._rest_state(original_data) == original_rest
                                 and set(preview.bones.keys()) == set(original_data.bones.keys())
                                 and all(ua._same_rest_bone(original_data.bones[name], bone)
                                         for name, bone in preview.bones.items()),
                                 'A temporary reference data copy changed; preserve it for inspection.')
                        ua._swap_armature_data(context, rig, original_data)
                    if action is not None and rig.get(ua.ACTIVE_KEY) is action:
                        rig.pop(ua.ACTIVE_KEY, None)
                    composition._restore_prior(context, rig, prior)
                    _restore_live_pose(rig, prior)
                    if preview is not None:
                        _require(preview.users == int(preview.use_fake_user),
                                 'The temporary reference data gained another owner; preserve it.')
                        preview.use_fake_user = False
                        ua._discard_preview_data(action, preview)
                except Exception as error:
                    errors.append(str(error))
                if errors:
                    primary.add_note('Reference import recovery: ' + '; '.join(errors))
                raise
        _reference_admission(context, session, action, slot, reference_packet_file,
            reference_packet_sha256, model_file, model_sha256, _creating=True)
        _require(_motion(session['base_action'], session['base_slot']) == base_motion
                 and _motion(session['input_action'], session['input_slot']) == input_motion,
                 'Preparing a reference changed the formal base or twenty input curves.')
        _validate(session)
        return action, slot
    except Exception as primary:
        if action is not None and action not in actions_before:
            try:
                _require(rig.get(ua.ACTIVE_KEY) is not action
                         and rig.animation_data.action is not action
                         and action.users == int(action.use_fake_user),
                         'The failed reference Action still has another owner; preserve it.')
                action.use_fake_user = False
                bpy.data.actions.remove(action)
            except Exception as error:
                primary.add_note('Failed reference Action retained: ' + str(error))
        raise


def set_preview_reference(context, session, reference_action=None, reference_slot=None, *,
                          reference_packet_file=None, reference_packet_sha256=None,
                          model_file=None, model_sha256=None):
    """Preview a sealed read-only base on normalized input phase; never rebind export.

    Supply an actual native-clock import restored with Unity's public preview
    recovery, including its original target/scene metadata. Reference packet
    Rigging/weapon data is never imported or solved here. Call in BASE role with
    native Tweak ended. Passing no reference restores the formal base strip.
    """
    _validate(session)
    rig, pointer = session['rig'], session['rig'].as_pointer()
    _require(context.scene is session['scene'] and session['tracks'] and session['enabled']
             and not session['source_read_only'] and session['edit_role'] == 'BASE'
             and not session['tweak_enabled'] and not rig.animation_data.use_tweak_mode
             and not _SUSPENDED.get(pointer, 0) and pointer not in _BUSY,
             'Change the reference on its enabled Custom BASE preview outside Tweak/export.')
    if reference_action is None:
        _require(all(value is None for value in (reference_slot, reference_packet_file,
            reference_packet_sha256, model_file, model_sha256)), 'Restore BASE without replacement provenance.')
        reference = None
    else:
        reference = _reference_admission(context, session, reference_action, reference_slot,
            reference_packet_file, reference_packet_sha256, model_file, model_sha256)
    before = _detached(session)
    prior = composition._prior(context, rig)
    base_motion, input_motion = _motion(session['base_action'], session['base_slot']), _motion(session['input_action'], session['input_slot'])
    strip = _tracks(session)['BASE'].strips[0]
    old = before['tracks']['BASE']
    _BUSY.add(pointer)
    try:
        action, slot, timing = _preview_base_timing(session, reference)
        _assign_preview_base(strip, action, slot, timing)
        if reference is None:
            session.pop('preview_reference', None)
        else:
            session['preview_reference'] = reference
        session['tracks']['BASE'] = {'name': old['name'], 'action': action, 'slot': slot,
            'fields': {key: composition._plain(getattr(strip, key)) for key in composition._STRIP_FIELDS}}
        ua._set_frame(context.scene, context.scene.frame_current + context.scene.frame_subframe)
        _apply(context, session)
        _require(_motion(session['base_action'], session['base_slot']) == base_motion
                 and _motion(session['input_action'], session['input_slot']) == input_motion,
                 'Changing a preview reference modified the independent base or twenty input curves.')
        _validate(session)
        _store(rig, session)
        _HANDLER_ERRORS.pop(pointer, None)
    except Exception as primary:
        errors = []
        try:
            strip.action, strip.action_slot = old['action'], composition._slot(old['action'], old['slot'])
            for key, value in old['fields'].items():
                if key not in ('frame_start', 'frame_end'):
                    setattr(strip, key, value)
            strip.frame_start, strip.frame_end = old['fields']['frame_start'], old['fields']['frame_end']
        except Exception as error:
            errors.append('prior reference strip: ' + str(error))
        session.clear()
        session.update(before)
        for label, operation in (('prior native state', lambda: composition._restore_prior(context, rig, prior)),
                ('live preview', lambda: _restore_live_pose(rig, prior)),
                ('persistent owner', lambda: _store(rig, session))):
            try:
                operation()
            except Exception as error:
                errors.append(label + ': ' + str(error))
        if errors:
            primary.add_note('Preview reference recovery: ' + '; '.join(errors))
        raise
    finally:
        _BUSY.discard(pointer)
    return session


def _make_tracks(context, session):
    rig, ad = session['rig'], session['rig'].animation_data_create()
    _require(not ad.use_tweak_mode, 'Leave the existing native Tweak session before Rigging preview.')
    for index, side in enumerate(_SIDES):
        for field, _count in _FIELDS:
            key = _property(side, field)
            session['properties_before'][key] = {'present': key in rig,
                'value': list(rig[key]) if key in rig else None}
            rig[key] = session['capture']['frames'][0]['controls'][index][field]
    ad.action = None
    ad.use_nla = True
    for track in ad.nla_tracks:
        track.mute, track.is_solo = True, False
    start = context.scene.frame_start
    scene_rate = context.scene.render.fps / context.scene.render.fps_base
    _require(math.isfinite(scene_rate) and scene_rate > 0, 'The owning Scene has no valid frame clock.')
    context.scene.frame_end = start + max(1, math.ceil(session['capture']['duration'] * scene_rate))
    context.scene.frame_preview_start, context.scene.frame_preview_end = start, context.scene.frame_end
    session['scene_clock'] = [context.scene.render.fps, context.scene.render.fps_base]
    session['scene_start'] = start
    for role, action, slot in (('BASE', session['base_action'], session['base_slot']),
                               ('RIGGING', session['input_action'], session['input_slot'])):
        rate = (ua.native_action_frame_rate(action) if role == 'BASE'
                else session['capture']['sampleRate'])
        _require(rate is not None, 'Base Rigging preview requires the authenticated native Action clock.')
        _require(rate == session['capture']['sampleRate'], 'Base and Target input clocks are not the same authenticated packet clock.')
        duration = session['capture']['duration']
        begin = float(action.get('unity_start_frame', 1.)) if role == 'BASE' else 1.
        end = begin + max(duration * rate, 1.)
        before_names = set(track.name for track in ad.nla_tracks)
        try:
            track = composition._track(ad, 'Rigging Preview · ' + role.title(), action, slot,
                begin, end, speed=rate / scene_rate, scene_start=start,
                loop=False, view_end=context.scene.frame_end)
        except Exception:
            # The native strip constructor can fail after creating its track.
            # Only the new track from this one synchronous call is owned here.
            for added in list(ad.nla_tracks):
                if added.name not in before_names:
                    _require(len(added.strips) <= 1 and (not added.strips
                             or added.strips[0].action is action),
                             'A new foreign NLA dependency was retained after import failure.')
                    ad.nla_tracks.remove(added)
            raise
        strip = track.strips[0]
        if role == 'BASE' and session.get('preview_reference') is not None:
            selected, selected_slot, timing = _preview_base_timing(session, session['preview_reference'])
            _assign_preview_base(strip, selected, selected_slot, timing)
            action, slot = selected, selected_slot
        session['tracks'][role] = {'name': track.name, 'action': action, 'slot': slot,
            'fields': {field: composition._plain(getattr(strip, field))
                       for field in composition._STRIP_FIELDS}}


def _time(context, session):
    seconds = ((context.scene.frame_current + context.scene.frame_subframe - session['scene_start'])
               / (context.scene.render.fps / context.scene.render.fps_base))
    duration = session['capture']['duration']
    if duration <= 0:
        return 0.
    # One clip has one inclusive endpoint, including a fractional Scene frame.
    # Both strips repeat once and HOLD there; Scene playback returns to start.
    # A duration modulo here would wrap Target inputs while BASE is holding.
    return min(duration, max(0., seconds))


def _sample(session, time, *, curves=None):
    if curves is None:
        curves = _curves(session['input_action'], session['input_slot'])
    frame = 1. + time * session['capture']['sampleRate']
    controls = []
    for side in _SIDES:
        row = {}
        for field, count in _FIELDS:
            path = '[' + json.dumps(_property(side, field)) + ']'
            row[field] = [float(curves[path, component].evaluate(frame)) for component in range(count)]
        q = Quaternion((row['targetRotation'][3], *row['targetRotation'][:3]))
        _require(sum(v * v for v in q) > 1e-16 and all(math.isfinite(v) for values in row.values() for v in values),
                 'The keyed Rigging inputs are non-finite or have a zero rotation.')
        q.normalize()
        row['targetRotation'] = [q.x, q.y, q.z, q.w]
        controls.append(row)
    return controls


def _parent_at(session, index, field, time):
    binding = session['capture']['bindings'][index]
    if index == 1 and field == 'targetParentRoot' and binding['targetParentPath'] == session['capture']['bindings'][0]['tipPath']:
        # Transport the authenticated Unity bone frame through the CURRENT
        # native RightHand.  Never replay its old sampled parent after a drag.
        rig, row = session['rig'], session['native_bindings'][0]
        c = _matrix(session['conversion'])
        return (c.inverted() @ rig.matrix_world @ rig.pose.bones[row['chain'][2]].matrix
                @ _matrix(row['native_tip_rest']).inverted() @ c @ _matrix(row['unity_tip_rest']))
    frames = session['capture']['frames']
    # Parent transforms are read-only source inputs; use an exact bounded
    # TRS interpolation, never interpolate matrix coefficients through shear.
    before = max((frame for frame in frames if frame['time'] <= time),
                 key=lambda frame: frame['time'], default=frames[0])
    after = min((frame for frame in frames if frame['time'] >= time),
                key=lambda frame: frame['time'], default=frames[-1])
    first, last = _matrix(before['controls'][index][field]), _matrix(after['controls'][index][field])
    if before['time'] == after['time']:
        return first
    a, ar, asc = first.decompose()
    b, br, bsc = last.decompose()
    factor = (time - before['time']) / (after['time'] - before['time'])
    return Matrix.LocRotScale(a.lerp(b, factor), ar.slerp(br, factor), asc.lerp(bsc, factor))


def _tip_from_unity(session, index, unity_matrix):
    row, c = session['native_bindings'][index], _matrix(session['conversion'])
    return c @ unity_matrix @ _matrix(row['unity_tip_rest']).inverted() @ c.inverted() @ _matrix(row['native_tip_rest'])


def _unity_from_tip(session, index, native_matrix):
    row, c = session['native_bindings'][index], _matrix(session['conversion'])
    return c.inverted() @ native_matrix @ _matrix(row['native_tip_rest']).inverted() @ c @ _matrix(row['unity_tip_rest'])


@contextmanager
def _natural_end(context, rig, entry):
    offset = entry.get('auto_offset_rotation') if entry['auto_align'] else None
    before = offset.mute if offset else None
    try:
        if offset:
            offset.mute = True
            limb_ik_fk._update(context, rig)
        yield rig.pose.bones[entry['chain'][2]].matrix.copy()
    finally:
        if offset:
            offset.mute = before
            limb_ik_fk._update(context, rig)


def _write_target(context, session, index, entry, desired):
    rig, target = session['rig'], session['rig'].pose.bones[entry['target'].name]
    desired = rig.matrix_world.inverted() @ desired
    limb_ik_fk._set_solver_position(context, rig, entry, desired.translation)
    if entry['auto_align']:
        with _natural_end(context, rig, entry) as natural:
            if entry['auto_rotation_space'] == 'PARENT_DELTA':
                target.matrix = limb_ik._auto_target_rotation_matrix(rig, target, natural, desired)
            else:
                end = rig.pose.bones[entry['chain'][2]]
                a = rig.convert_space(pose_bone=end, matrix=natural, from_space='POSE', to_space='LOCAL')
                b = rig.convert_space(pose_bone=end, matrix=desired, from_space='POSE', to_space='LOCAL')
                target.matrix_basis = Matrix.LocRotScale(target.matrix_basis.translation,
                    a.to_quaternion().inverted() @ b.to_quaternion(), target.matrix_basis.to_scale())
    else:
        target.matrix = Matrix.LocRotScale(target.matrix.translation,
            desired.to_quaternion(), target.matrix.to_scale())
    limb_ik_fk._update(context, rig)


def _read_target(context, session, entry):
    rig, target = session['rig'], session['rig'].pose.bones[entry['target'].name]
    if not entry['auto_align']:
        return rig.matrix_world @ target.matrix
    with _natural_end(context, rig, entry) as natural:
        if entry['auto_rotation_space'] == 'PARENT_DELTA':
            neutral = limb_ik._neutral_target_matrix(target).to_quaternion().normalized()
            rotation = target.matrix.to_quaternion().normalized() @ neutral.inverted() @ natural.to_quaternion().normalized()
            matrix = Matrix.LocRotScale(target.matrix.translation, rotation, Vector((1., 1., 1.)))
        else:
            end = rig.pose.bones[entry['chain'][2]]
            local = rig.convert_space(pose_bone=end, matrix=natural, from_space='POSE', to_space='LOCAL')
            local = Matrix.LocRotScale(local.translation,
                local.to_quaternion() @ target.matrix_basis.to_quaternion(), local.to_scale())
            matrix = rig.convert_space(pose_bone=end, matrix=local, from_space='LOCAL', to_space='POSE')
            matrix.translation = target.matrix.translation
        return rig.matrix_world @ matrix


def _apply(context, session):
    # Original20's post-FK IK transaction must publish Forearm output only
    # after the final pose/mode (including rollback); ordinary paths are inert.
    from contextlib import nullcontext
    scope = nullcontext()
    if 'original_inputs' in session:
        from . import forearm_twist
        scope = forearm_twist.defer_runtime(context)
    with scope:
        _validate(session)
        rig, inventory = session['rig'], _inventory(session)
        time, c = _time(context, session), _matrix(session['conversion'])
        values = _sample(session, time)
        # The ordinary base importer keys exact FK=0.  A frame update must have
        # evaluated that base before this explicit post-frame preview step.
        base = {}
        for row in session['native_bindings']:
            entry = inventory['rigs']['ARM', row['native_side']]
            _require(limb_ik_fk.mode_for_rig(rig, entry) == 'FK',
                     'Evaluate the independent base FK Action before applying Rigging inputs.')
            base[row['native_side']] = {name: rig.pose.bones[name].matrix.copy() for name in entry['chain']}
        for index, (binding, row, inputs) in enumerate(zip(session['capture']['bindings'], session['native_bindings'], values)):
            entry = inventory['rigs']['ARM', row['native_side']]
            enabled = binding['constraintWeight'] == 1.
            if enabled:
                limb_ik_fk.switch_limb(context, rig, ('ARM', row['native_side']), 'IK',
                    keyframe=False, desired_pose=base[row['native_side']], sync_display=False)
            unity = _parent_at(session, index, 'targetParentRoot', time) @ Matrix.LocRotScale(
                Vector(inputs['targetPosition']), Quaternion((inputs['targetRotation'][3], *inputs['targetRotation'][:3])), Vector((1., 1., 1.)))
            desired = _tip_from_unity(session, index, unity)
            native_base = rig.matrix_world @ base[row['native_side']][row['chain'][2]]
            desired = Matrix.LocRotScale(desired.translation if binding['targetPositionWeight'] else native_base.translation,
                desired.to_quaternion() if binding['targetRotationWeight'] else native_base.to_quaternion(), Vector((1., 1., 1.)))
            if binding['hintWeight']:
                point = c @ (_parent_at(session, index, 'hintParentRoot', time) @ Vector(inputs['hintPosition']))
                pole = rig.pose.bones[row['pole']]
                matrix = pole.matrix.copy()
                matrix.translation = rig.matrix_world.inverted() @ point
                pole.matrix = matrix
                limb_ik_fk._update(context, rig)
            # Auto Align encodes its offset against the solved natural hand.
            # Finalize this arm's Pole first, then its Target, before the Left
            # input reads the current RightHand as its dynamic parent.
            _write_target(context, session, index, entry, desired)
        session['_time'] = time
        session['_left_parent'] = _rows(_parent_at(session, 1, 'targetParentRoot', time))
        # Matching defers display until both arms and their Targets are final.
        # Original20 still needs the official ring/Body membership handoff;
        # retain the artist's hide flags while synchronizing those owned outputs.
        bone_collections.sync_limb_display(rig, inventory,
            keys=tuple(('ARM', row['native_side']) for row in session['native_bindings']),
            force_flags='original_inputs' not in session)
        _restrict_inputs(session)


@contextmanager
def _context(session):
    # Reacquire a real window every callback; never retain an override over a
    # timer, file reload, or generator suspension.
    windows = [window for window in bpy.context.window_manager.windows if window.scene is session['scene']]
    _require(len(windows) == 1, 'Rigging preview needs its exact owning scene window.')
    with bpy.context.temp_override(window=windows[0]):
        _require(bpy.context.scene is session['scene'] and session['rig'].name in bpy.context.view_layer.objects,
                 'The Rigging preview Rig is outside its owned view layer.')
        yield bpy.context


@bpy.app.handlers.persistent
def _frame(scene, *_args):
    for rig in scene.objects:
        pointer = rig.as_pointer()
        if SESSION_KEY not in rig or pointer in _BUSY or pointer in _HANDLER_ERRORS or _SUSPENDED.get(pointer, 0):
            continue
        session = None
        _BUSY.add(pointer)
        try:
            session = current(rig)
            if session['scene'] is not scene or not session['enabled']:
                continue
            with _context(session) as context:
                _apply(context, session)
        except Exception as error:
            _handler_error(rig, session, error)
        finally:
            _BUSY.discard(pointer)


@bpy.app.handlers.persistent
def _depsgraph(scene, *_args):
    # Only the actual Left Target -> RightHand relation is refreshed here.
    # The three other inputs and current unkeyed Target/Pole drags are NEVER
    # replayed from the Action during a dependency update.
    for rig in scene.objects:
        pointer = rig.as_pointer()
        if SESSION_KEY not in rig or pointer in _BUSY or pointer in _HANDLER_ERRORS or _SUSPENDED.get(pointer, 0):
            continue
        session = None
        _BUSY.add(pointer)
        try:
            session = current(rig)
            if session['scene'] is not scene or not session['enabled'] or '_left_parent' not in session:
                continue
            with _context(session) as context:
                from contextlib import nullcontext
                scope = nullcontext()
                if 'original_inputs' in session:
                    inventory = _inventory(session)
                    mode_changed = any(binding['constraintWeight'] and
                        limb_ik_fk.mode_for_rig(rig, inventory['rigs']['ARM', row['native_side']]) != 'IK'
                        for binding, row in zip(session['capture']['bindings'], session['native_bindings']))
                    binding = session['capture']['bindings'][1]
                    parent_changed = False
                    if binding['constraintWeight']:
                        before = _matrix(session['_left_parent'])
                        after = _parent_at(session, 1, 'targetParentRoot', session['_time'])
                        parent_changed = max(abs(a - b) for a, b in zip(_rows(before), _rows(after))) > 1e-7
                    if not mode_changed and not parent_changed:
                        continue
                    from . import forearm_twist
                    scope = forearm_twist.defer_runtime(context)
                # Re-read the dynamic parent after restoring the final native mode.
                # Nested mode restoration defers to this callback's final Target.
                with scope:
                    _restore_preview_mode(context, session)
                    binding = session['capture']['bindings'][1]
                    if not binding['constraintWeight']:
                        continue
                    time = session['_time']
                    before = _matrix(session['_left_parent'])
                    after = _parent_at(session, 1, 'targetParentRoot', time)
                    if max(abs(a - b) for a, b in zip(_rows(before), _rows(after))) <= 1e-7:
                        continue
                    entry = _inventory(session)['rigs']['ARM', 'L']
                    unity = _unity_from_tip(session, 1, _read_target(context, session, entry))
                    local = before.inverted() @ unity
                    wanted = _tip_from_unity(session, 1, after @ local)
                    present = _read_target(context, session, entry)
                    wanted = Matrix.LocRotScale(wanted.translation if binding['targetPositionWeight'] else present.translation,
                        wanted.to_quaternion() if binding['targetRotationWeight'] else present.to_quaternion(), Vector((1., 1., 1.)))
                    _write_target(context, session, 1, entry, wanted)
                    session['_left_parent'] = _rows(after)
        except Exception as error:
            _handler_error(rig, session, error)
        finally:
            _BUSY.discard(pointer)


def _handler_error(rig, session, error):
    # Stop this owned callback after a failed admission, rather than throw on
    # every evaluation or attempt to repair a corrupt owner record.
    _HANDLER_ERRORS[rig.as_pointer()] = str(error)
    if session is not None and session.get('rig') is rig:
        session['preview_error'], session['enabled'] = str(error), False
        try:
            _store(rig, session)
        except Exception as store_error:
            _HANDLER_ERRORS[rig.as_pointer()] += '; cannot persist error: ' + str(store_error)


def _restore_preview_mode(context, session):
    rig, inventory = session['rig'], _inventory(session)
    if not any(binding['constraintWeight'] and
            limb_ik_fk.mode_for_rig(rig, inventory['rigs']['ARM', row['native_side']]) != 'IK'
            for binding, row in zip(session['capture']['bindings'], session['native_bindings'])):
        return
    # Original20's post-FK IK transaction must publish Forearm output only
    # after the final pose/mode (including rollback); ordinary paths are inert.
    from contextlib import nullcontext
    scope = nullcontext()
    if 'original_inputs' in session:
        from . import forearm_twist
        scope = forearm_twist.defer_runtime(context)
    with scope:
        # A dependency reevaluation may key BASE's FK=0 again at the same frame.
        # Reestablish the original native IK mode without replaying source inputs
        # or overwriting current unkeyed Target/Pole/ORI poses.
        for binding, row in zip(session['capture']['bindings'], session['native_bindings']):
            entry = inventory['rigs']['ARM', row['native_side']]
            if not binding['constraintWeight'] or limb_ik_fk.mode_for_rig(rig, entry) == 'IK':
                continue
            names = {row['target'], row['pole']} | {entry[key].name for key in ('ori_upper', 'ori_lower') if entry.get(key)}
            bases = {name: rig.pose.bones[name].matrix_basis.copy() for name in names}
            desired = {name: rig.pose.bones[name].matrix.copy() for name in row['chain']}
            limb_ik_fk.switch_limb(context, rig, ('ARM', row['native_side']), 'IK',
                keyframe=False, desired_pose=desired, sync_display=False)
            for name, matrix in bases.items():
                rig.pose.bones[name].matrix_basis = matrix
            limb_ik_fk._update(context, rig)
        if 'original_inputs' in session:
            bone_collections.sync_limb_display(rig, inventory,
                keys=tuple(('ARM', row['native_side']) for row in session['native_bindings']), force_flags=False)


def _handlers():
    if _frame not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_frame)
    if _depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph)


def _remove_handlers_if_idle():
    for rig in bpy.data.objects:
        if SESSION_KEY in rig:
            # Unreadable ownership is not permission to remove a callback
            # serving another Rig.  Leave shared registrations in that case.
            try:
                if json.loads(rig[SESSION_KEY])['payload']['enabled']:
                    return
            except (KeyError, TypeError, ValueError):
                return
    for collection, handler in ((bpy.app.handlers.frame_change_post, _frame),
                                (bpy.app.handlers.depsgraph_update_post, _depsgraph)):
        if handler in collection:
            collection.remove(handler)


def activate(context, session, *, edit_role='BASE', enabled=False):
    """Show both layers; optional explicit Tweak selects the actual edit owner."""
    _require(edit_role in {'BASE', 'RIGGING'} and type(enabled) is bool,
             'Choose BASE or RIGGING and an explicit native-editing flag.')
    _validate(session)
    _require('original_inputs' not in session or edit_role != 'BASE' or not enabled,
             'Original control inputs keep Source and Body read-only; edit Target/Hint instead.')
    _require(not session.get('preview_reference') or edit_role != 'BASE' or not enabled,
             'The preview reference is read-only; restore the formal base before BASE Tweak.')
    _require(context.scene is session['scene'], 'Activate Rigging in its saved owning scene.')
    _require(not session['source_read_only'] or edit_role == 'BASE' and not enabled,
             'Source Rigging inputs are read-only; activate Custom to edit.')
    rig, pointer = session['rig'], session['rig'].as_pointer()
    prior, display = composition._prior(context, rig), bone_display._snapshot(rig)
    before = _detached({key: value for key, value in session.items() if not key.startswith('_')})
    volatile = {key: deepcopy(value) for key, value in session.items() if key.startswith('_')}
    # Original20's post-FK IK transaction must publish Forearm output only
    # after the final pose/mode (including rollback); ordinary paths are inert.
    from contextlib import nullcontext
    scope = nullcontext()
    if 'original_inputs' in session:
        from . import forearm_twist
        scope = forearm_twist.defer_runtime(context)
    with scope:
        _BUSY.add(pointer)
        try:
            if rig.animation_data and rig.animation_data.use_tweak_mode:
                tracks = _tracks(session)
                _require(session['tweak_enabled'], 'Another native Tweak session is active.')
                composition._tweak(context, rig, tracks[session['edit_role']], enabled=False)
            if not session['tracks']:
                _make_tracks(context, session)
            tracks = _tracks(session)
            for track in tracks.values():
                track.mute = False
            session['edit_role'], session['tweak_enabled'] = edit_role, enabled
            session['enabled'] = bool(session['capture']['previewEnabled'])
            context.scene.tool_settings.use_keyframe_insert_auto = False
            if enabled:
                composition._tweak(context, rig, tracks[edit_role], enabled=True)
            ua._set_frame(context.scene, context.scene.frame_current + context.scene.frame_subframe)
            if session['enabled']:
                _apply(context, session)
            else:
                _restrict_inputs(session)
            _store(rig, session)
            _HANDLER_ERRORS.pop(pointer, None)
        except Exception as primary:
            errors = []
            try:
                if rig.animation_data and rig.animation_data.use_tweak_mode:
                    composition._tweak(context, rig, None, enabled=False)
            except Exception as error:
                errors.append('leave owned Tweak: ' + str(error))
            if not before['tracks']:
                for row in session['tracks'].values():
                    try:
                        track = rig.animation_data.nla_tracks.get(row['name'])
                        _require(track is not None and len(track.strips) == 1
                                 and track.strips[0].action is row['action']
                                 and track.strips[0].action_slot_handle == row['slot'],
                                 'A partially-created preview track changed; it was retained.')
                        rig.animation_data.nla_tracks.remove(track)
                    except Exception as error:
                        errors.append('new owned track: ' + str(error))
                for key, row in session['properties_before'].items():
                    try:
                        if row['present']:
                            rig[key] = row['value']
                        else:
                            rig.pop(key, None)
                    except Exception as error:
                        errors.append('prior input property: ' + str(error))
            session.clear()
            session.update(before)
            session.update(volatile)
            for label, operation in (
                    ('prior native state', lambda: composition._restore_prior(context, rig, prior)),
                    ('display', lambda: bone_display._restore(rig, display))):
                try:
                    operation()
                except Exception as error:
                    errors.append(label + ': ' + str(error))
            try:
                if before['tweak_enabled']:
                    composition._tweak(context, rig, _tracks(session)[before['edit_role']], enabled=True)
                    _restore_live_pose(rig, prior)
                _store(rig, session)
            except Exception as error:
                errors.append('owned session: ' + str(error))
            if errors:
                primary.add_note('Rigging activation recovery: ' + '; '.join(errors))
            raise
        finally:
            _BUSY.discard(pointer)
        if session['enabled']:
            _handlers()
        return session


def _restrict_inputs(session):
    rig, inventory = session['rig'], _inventory(session)
    controlled = {row['rig_id'] for row in session['native_bindings']}
    covered = {name for row in session['native_bindings'] for name in row['chain']}
    covered.update(bone.name for bone in inventory['bones'] if bone.get(limb_ik.RIG_ID_KEY) in controlled)
    targets = {row['target']: binding for row, binding in zip(session['native_bindings'], session['capture']['bindings'])}
    poles = {row['pole']: binding for row, binding in zip(session['native_bindings'], session['capture']['bindings'])}
    for pb in rig.pose.bones:
        before = session['prior']['locks'][pb.name]
        wanted = {'lock_location': before['location'], 'lock_rotation': before['rotation'],
                  'lock_scale': before['scale'], 'lock_rotation_w': before['rotation_w'],
                  'lock_rotations_4d': before['rotation_4d']}
        selectable = before['select']
        permitted = (pb.name not in covered and 'original_inputs' not in session if session['edit_role'] == 'BASE'
                     else pb.name in targets or pb.name in poles)
        if not permitted:
            wanted.update(lock_location=(True,) * 3, lock_rotation=(True,) * 3,
                          lock_scale=(True,) * 3, lock_rotation_w=True)
            selectable = True
            owner = composition._pose_selection_owner(pb)
            if owner.select:
                owner.select = False
        elif session['edit_role'] == 'RIGGING':
            binding = targets.get(pb.name) or poles[pb.name]
            is_target = pb.name in targets
            position = binding['targetPositionWeight'] if is_target else binding['hintWeight']
            rotation = binding['targetRotationWeight'] if is_target else 0
            wanted.update(lock_location=(not bool(position),) * 3, lock_rotation=(not bool(rotation),) * 3,
                          lock_scale=(True,) * 3, lock_rotation_w=not bool(rotation))
            selectable = session['source_read_only'] or not bool(binding['constraintWeight'])
            if hasattr(pb, 'hide') and pb.hide:
                pb.hide = False
            if pb.bone.hide:
                pb.bone.hide = False
            body = bone_collections.body_collection(rig)
            if body is not None and pb.name not in body.bones and 'original_inputs' not in session:
                body.assign(pb.bone)
        for field, value in wanted.items():
            actual = getattr(pb, field)
            changed = list(actual) != list(value) if isinstance(value, (tuple, list)) else actual != value
            if changed:
                setattr(pb, field, value)
        if pb.bone.hide_select != selectable:
            pb.bone.hide_select = selectable


def key_current(context, session):
    """Explicit Save Rigging: key local source inputs, never solved bones."""
    _validate(session)
    _require(session['enabled'] and session['edit_role'] == 'RIGGING'
             and not session['source_read_only'] and not _SUSPENDED.get(session['rig'].as_pointer(), 0),
             'Activate editable Rigging inputs before Save Rigging.')
    _require(context.scene is session['scene'] and context.active_object is session['rig']
             and session['rig'].mode == 'POSE', 'Save Rigging on its owning Rig in Pose mode.')
    rig, pointer = session['rig'], session['rig'].as_pointer()
    ad = rig.animation_data
    _tracks(session)
    _require(ad is not None and ad.use_nla
             and ((session['tweak_enabled'] and ad.use_tweak_mode
                   and ad.action is session['input_action'] and ad.action_slot
                   and ad.action_slot.handle == session['input_slot'])
                  or (not session['tweak_enabled'] and not ad.use_tweak_mode and ad.action is None)),
             'The exact editable input carrier/slot is no longer active.')
    saved = [composition._curve_payload(curve)
             for curve in _curves(session['input_action'], session['input_slot']).values()]
    before_motion, before_commit = _motion(session['input_action'], session['input_slot']), session['committed']
    _BUSY.add(pointer)
    try:
        time = _time(context, session)
        values = _sample(session, time)
        inventory, c = _inventory(session), _matrix(session['conversion'])
        for index, (binding, row) in enumerate(zip(session['capture']['bindings'], session['native_bindings'])):
            entry = inventory['rigs']['ARM', row['native_side']]
            local = _parent_at(session, index, 'targetParentRoot', time).inverted() @ _unity_from_tip(
                session, index, _read_target(context, session, entry))
            if binding['targetPositionWeight']:
                values[index]['targetPosition'] = list(local.translation)
            if binding['targetRotationWeight']:
                q = local.to_quaternion().normalized()
                old = Quaternion((values[index]['targetRotation'][3], *values[index]['targetRotation'][:3]))
                if q.dot(old) < 0:
                    q.negate()
                values[index]['targetRotation'] = [q.x, q.y, q.z, q.w]
            if binding['hintWeight']:
                world = rig.matrix_world @ rig.pose.bones[row['pole']].matrix.translation
                point = _parent_at(session, index, 'hintParentRoot', time).inverted() @ (c.inverted() @ world)
                values[index]['hintPosition'] = list(point)
        _require(all(math.isfinite(v) for row in values for component in row.values() for v in component),
                 'The current Target/Hint inputs are non-finite.')
        curves = _curves(session['input_action'], session['input_slot'])
        frame = 1. + time * session['capture']['sampleRate']
        for index, side in enumerate(_SIDES):
            for field, count in _FIELDS:
                path = '[' + json.dumps(_property(side, field)) + ']'
                for component in range(count):
                    # Default insertion replaces a matching key and adds an
                    # exact new phase when the Scene/native clocks differ.
                    # REPLACE alone forbids adding a missing key in Blender.
                    point = curves[path, component].keyframe_points.insert(frame, values[index][field][component])
                    _require(point is not None, 'The native input key was not inserted.')
                    point.interpolation = 'LINEAR'
                    curves[path, component].update()
        _curves(session['input_action'], session['input_slot'])
        session['committed'] = True
        _store(rig, session)
        return deepcopy(values)
    except Exception as primary:
        try:
            bag = composition._bag(session['input_action'], session['input_slot'])
            for curve in list(bag.fcurves):
                bag.fcurves.remove(curve)
            for row in saved:
                composition._write_curve(bag, row)
            _require(_motion(session['input_action'], session['input_slot']) == before_motion,
                     'The prior input keys were not restored exactly; the carrier was retained.')
            session['committed'] = before_commit
            _store(rig, session)
        except Exception as error:
            primary.add_note('Save Rigging input recovery: ' + str(error))
        raise
    finally:
        _BUSY.discard(pointer)


def capture_inputs(context, session, *, source_packet_sha256, work_item_id,
                   source_action, source_action_slot):
    """Read-only Return DTO from saved carrier keys, with external ownership."""
    _validate(session)
    expected_action = source_action.name if isinstance(source_action, bpy.types.Action) else source_action
    _require(not isinstance(source_action, bpy.types.Action) or source_action is session['base_action'],
             'The supplied base Action pointer belongs to another native owner.')
    _require(source_packet_sha256 == session['source_packet_sha256']
             and _uid(work_item_id) == session['work_item_id']
             and expected_action == session['base_action'].name
             and source_action_slot == session['base_slot'],
             'Rigging export identities do not match the bound base and input Actions.')
    # This synchronous read does not change frames or yield to Blender. Validate
    # the saved curves once for this export, retaining per-sample value checks.
    curves = _curves(session['input_action'], session['input_slot'])
    samples = [{'time': frame['time'], 'controls': _sample(session, frame['time'], curves=curves)}
               for frame in session['capture']['frames']]
    return contract.build_export(session['capture'], samples,
        source_packet_sha256=source_packet_sha256, work_item_id=work_item_id,
        new_action_id=session['new_action_id'], source_action=expected_action,
        source_action_slot=source_action_slot, target_input_action=session['input_action'].name,
        target_input_action_slot=session['input_slot'])


@contextmanager
def base_only(context, rig, action=None):
    """Nest-safe isolation of preview inputs across all base export admission."""
    session = current(rig)
    if session is None:
        yield
        return
    _require('original_inputs' not in session,
             'Publish the twenty control inputs separately; their Source is not a Body export Action.')
    _require(action is None or action is session['base_action'],
             'Base-only export must use the exact bound base Action.')
    pointer = rig.as_pointer()
    depth = _SUSPENDED.get(pointer, 0)
    if depth:
        _SUSPENDED[pointer] = depth + 1
        try:
            yield
        finally:
            _SUSPENDED[pointer] -= 1
        return
    prior, display = composition._prior(context, rig), bone_display._snapshot(rig)
    tracks = _tracks(session) if session['tracks'] else {}
    base_frame = (float(session['base_action']['unity_start_frame']) + _time(context, session) * ua.native_action_frame_rate(session['base_action'])
                  if tracks else prior['snapshot']['frame'])
    _SUSPENDED[pointer] = 1
    _BUSY.add(pointer)
    primary = None
    try:
        if rig.animation_data and rig.animation_data.use_tweak_mode:
            _require(session['tweak_enabled'], 'A foreign Tweak session cannot be suspended for export.')
            composition._tweak(context, rig, tracks[session['edit_role']], enabled=False)
        for track in tracks.values():
            track.mute = True
        ad = rig.animation_data_create()
        ad.use_nla = False
        ad.action = session['base_action']
        ad.action_slot = composition._slot(session['base_action'], session['base_slot'])
        ua._set_frame(context.scene, base_frame)
        for row in session['native_bindings']:
            entry = _inventory(session)['rigs']['ARM', row['native_side']]
            _require(limb_ik_fk.mode_for_rig(rig, entry) == 'FK',
                     'The independent base Action does not restore exact FK inputs.')
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        errors = []
        try:
            try:
                composition._restore_prior(context, rig, prior)
            except Exception as error:
                errors.append('prior native state: ' + str(error))
            if session['enabled']:
                # Reevaluate FK base first; do not match the combined pose back
                # into the base.  Native preview and its unkeyed inputs recover
                # from the captured prior pose/control properties instead.
                if session['tweak_enabled']:
                    try:
                        composition._tweak(context, rig, _tracks(session)[session['edit_role']], enabled=True)
                    except Exception as error:
                        errors.append('owned Tweak: ' + str(error))
                try:
                    _restore_live_pose(rig, prior)
                except Exception as error:
                    errors.append('live input pose: ' + str(error))
            try:
                bone_display._restore(rig, display)
            except Exception as error:
                errors.append('display: ' + str(error))
        finally:
            _SUSPENDED.pop(pointer, None)
            _BUSY.discard(pointer)
        if errors:
            message = 'Base-only preview recovery: ' + '; '.join(errors)
            if primary is not None:
                primary.add_note(message)
            else:
                raise ValueError(message)


@contextmanager
def snapshot_base_refs(rig, action):
    """Hide only owned preview references during the synchronous base write."""
    session = current(rig)
    if session is None:
        yield
        return
    _require(_SUSPENDED.get(rig.as_pointer(), 0) > 0 and action is session['base_action']
             and rig.animation_data and rig.animation_data.action is action
             and rig.animation_data.action_slot_handle == session['base_slot']
             and not rig.animation_data.use_nla and not rig.animation_data.use_tweak_mode,
             'Write the base snapshot only inside its exact base-only boundary.')
    base_motion = _motion(action, session['base_slot'])
    input_motion = _motion(session['input_action'], session['input_slot'])
    rig_values = {SESSION_KEY: rig[SESSION_KEY], _REFS_KEY: dict(rig[_REFS_KEY])}
    if 'weapon_preview' in session:
        from . import animation_composition_weapon as weapon
        record, _objects = weapon.validate_owned(rig)
        _require(record['rigging_session_owner'] == session['owner']
                 and record['owner'] == session['weapon_preview']['display_owner']
                 and record['identity'] == session['weapon_preview']['identity'],
                 'The displayed weapon belongs to another Rigging session.')
        # Preserve nested native IDProperty groups independently of the live
        # root; removing that root must not invalidate the restoration copy.
        rig_values[weapon.RECORD] = bone_collections._copy_property(record)
        del record, _objects
    for side in _SIDES:
        for field, _count in _FIELDS:
            key = _property(side, field)
            if key in rig:
                rig_values[key] = list(rig[key])
    had_ref, input_ref = _INPUT_REF in action, action.get(_INPUT_REF)
    primary = None
    try:
        for key in rig_values:
            rig.pop(key, None)
        action.pop(_INPUT_REF, None)
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        errors = []
        for key, value in rig_values.items():
            try:
                rig[key] = value
            except Exception as error:
                errors.append(key + ': ' + str(error))
        try:
            if had_ref:
                action[_INPUT_REF] = input_ref
            else:
                action.pop(_INPUT_REF, None)
            _require(_motion(action, session['base_slot']) == base_motion
                     and _motion(session['input_action'], session['input_slot']) == input_motion,
                     'The base snapshot boundary changed an owned Action; the draft was retained.')
            _validate(current(rig))
        except Exception as error:
            errors.append('snapshot owner proof: ' + str(error))
        if errors:
            message = 'Base snapshot reference recovery: ' + '; '.join(errors)
            if primary is not None:
                primary.add_note(message)
            else:
                raise ValueError(message)


def _restore_live_pose(rig, prior):
    for name, fields in prior['snapshot']['pose'].items():
        pb = rig.pose.bones[name]
        pb.rotation_mode = fields['rotation_mode']
        for field, value in fields.items():
            if field != 'rotation_mode':
                setattr(pb, field, value)
    for name, values in prior['pb_properties'].items():
        for key, value in values.items():
            rig.pose.bones[name][key] = value
    rig.update_tag(refresh={'OBJECT'})


def restore_owned(context, rig):
    """Close preview, restore entry state, and retain committed input drafts."""
    session = current(rig)
    if session is None:
        return
    pointer = rig.as_pointer()
    token = None
    if 'weapon_preview' in session:
        from . import animation_composition_weapon as weapon
        token = next((value for value in reversed(_OWNED_TOKENS.get(pointer, ()))
                      if value['status'] == 'pending' and value['session']['owner'] == session['owner']), None)
        if token is None:
            # A rejected normal close must retain the native Tweak state too.
            # Transaction-owned displays follow the existing detach path.
            weapon.validate_removal(rig)
    # Original20's post-FK IK transaction must publish Forearm output only
    # after the final pose/mode (including rollback); ordinary paths are inert.
    from contextlib import nullcontext
    scope = nullcontext()
    if 'original_inputs' in session:
        from . import forearm_twist
        scope = forearm_twist.defer_runtime(context)
    with scope:
        _BUSY.add(pointer)
        _SUSPENDED[pointer] = _SUSPENDED.get(pointer, 0) + 1
        try:
            tracks = _tracks(session) if session['tracks'] else {}
            if rig.animation_data and rig.animation_data.use_tweak_mode:
                _require(session['tweak_enabled'], 'Close the foreign native Tweak session first.')
                composition._tweak(context, rig, tracks[session['edit_role']], enabled=False)
            if 'weapon_preview' in session:
                if token is None:
                    weapon.remove_rigging_context(context, rig, session=session)
                else:
                    _detach_weapon(rig, token)
            for track in tracks.values():
                rig.animation_data.nla_tracks.remove(track)
            for key, row in session['properties_before'].items():
                if row['present']:
                    rig[key] = row['value']
                else:
                    rig.pop(key, None)
            composition._restore_prior(context, rig, session['prior'])
            bone_display._restore(rig, session['display'])
            rig.pop(SESSION_KEY, None)
            rig.pop(_REFS_KEY, None)
            _CACHE.pop(pointer, None)
            _HANDLER_ERRORS.pop(pointer, None)
            # No successful carrier (including read-only Source), base Action,
            # original ID, cache, or authored key is removed on a normal close.
        finally:
            _SUSPENDED.pop(pointer, None)
            _BUSY.discard(pointer)
            _remove_handlers_if_idle()


def _capture_owned(context, rig):
    """Opaque in-process worklist rollback token, never a new baseline proof."""
    session = current(rig)
    if session is None:
        return None
    detached = _detached({key: value for key, value in session.items() if not key.startswith('_')})
    token = {'session': detached, 'status': 'pending',
            'live_prior': composition._prior(context, rig),
            'live': {key: deepcopy(session[key]) for key in ('_time', '_left_parent') if key in session},
            'input_motion': _motion(session['input_action'], session['input_slot']),
            'source_packet_sha256': session['source_packet_sha256']}
    if 'weapon_preview' in session:
        from . import animation_composition_weapon as weapon
        record, _objects = weapon.validate_owned(rig)
        _require(record.get('rigging_session_owner') == session['owner']
                 and record['owner'] == session['weapon_preview']['display_owner']
                 and record['identity'] == session['weapon_preview']['identity'],
                 'The transaction cannot retain another session weapon display.')
        token['weapon_record'] = bone_collections._copy_property(record)
        _OWNED_TOKENS.setdefault(rig.as_pointer(), []).append(token)
    return token


def _weapon_token(rig, token):
    _require(any(value is token for value in _OWNED_TOKENS.get(rig.as_pointer(), ()))
             and token['session']['rig'] is rig and 'weapon_record' in token,
             'The weapon recovery token is not this native transaction.')
    return token['weapon_record']


def _detach_weapon(rig, token):
    from . import animation_composition_weapon as weapon
    saved = _weapon_token(rig, token)
    record, _objects = weapon.validate_owned(rig)
    _require(token['status'] == 'pending' and bone_collections._copy_property(record) == saved,
             'The old weapon display changed before its transaction boundary.')
    root, collection = saved['scene'].collection, saved['collection']
    try:
        del rig[weapon.RECORD]
        root.children.unlink(collection)
        _require(collection.users == 0, 'A detached weapon display gained a foreign user.')
        token['status'] = 'detached'
    except Exception as primary:
        try:
            if root.children.get(collection.name) is not collection:
                root.children.link(collection)
            rig[weapon.RECORD] = saved
        except Exception as error:
            primary.add_note('Exact old display detachment recovery: ' + str(error))
        raise


def _return_weapon(rig, token):
    from . import animation_composition_weapon as weapon
    saved = _weapon_token(rig, token)
    root, collection = saved['scene'].collection, saved['collection']
    _require(token['status'] == 'detached' and weapon.RECORD not in rig
             and collection.users == 0 and root.children.get(collection.name) is None,
             'The retained display is no longer isolated in this transaction.')
    try:
        root.children.link(collection)
        rig[weapon.RECORD] = saved
        record, _objects = weapon.validate_owned(rig)
        _require(bone_collections._copy_property(record) == saved,
                 'The original weapon IDs and record were not restored exactly.')
        token['status'] = 'pending'
    except Exception as primary:
        try:
            if weapon.RECORD in rig and rig[weapon.RECORD].get('owner') == saved['owner']:
                del rig[weapon.RECORD]
            if root.children.get(collection.name) is collection:
                root.children.unlink(collection)
        except Exception as error:
            primary.add_note('Retained display return recovery: ' + str(error))
        raise


def _consume_token(rig, token, status):
    token['status'] = status
    pending = _OWNED_TOKENS.get(rig.as_pointer(), [])
    if any(value is token for value in pending):
        pending[:] = [value for value in pending if value is not token]
        if not pending:
            _OWNED_TOKENS.pop(rig.as_pointer(), None)


def _finish_owned_state(context, rig, token):
    """Release only the old display after its caller transaction commits."""
    if token is None or token['status'] in {'restored', 'finished'}:
        return
    _require(token['session']['rig'] is rig, 'Finish the original native Rig transaction.')
    if 'weapon_record' not in token or token['status'] == 'pending':
        _consume_token(rig, token, 'finished')
        return
    from . import animation_composition_weapon as weapon
    saved = _weapon_token(rig, token)
    if weapon.RECORD in rig:
        new_native_record, _objects = weapon.validate_owned(rig)
        new_record = bone_collections._copy_property(new_native_record)
    else:
        new_record = None
    _require(new_record is None or new_record['owner'] != saved['owner'],
             'The current display cannot be released as an obsolete one.')
    primary, removed = None, False
    try:
        rig.pop(weapon.RECORD, None)
        _return_weapon(rig, token)
        # Preserve every original collection/object/user-map guard. The
        # registered token authorizes only its captured old display IDs.
        weapon.remove_context(context, rig)
        removed = True
        _consume_token(rig, token, 'finished')
    except Exception as error:
        primary = error
        try:
            if weapon.RECORD in rig:
                _detach_weapon(rig, token)
        except Exception as recovery_error:
            primary.add_note('Obsolete display retention: ' + str(recovery_error))
        raise
    finally:
        if new_record is not None:
            try:
                rig[weapon.RECORD] = new_record
                weapon.validate_owned(rig)
            except Exception as error:
                if primary is not None:
                    primary.add_note('Current display record recovery: ' + str(error))
                else:
                    raise
        if not removed and primary is not None:
            primary.add_note('The old display was not released; no exact recovery is claimed after a partial native removal.')


def _restore_owned_state(context, rig, state):
    if state is None:
        return
    _require(current(rig) is None, 'Close the newly owned preview before worklist rollback.')
    session = state['session']
    _require(session['rig'] is rig and _motion(session['input_action'], session['input_slot']) == state['input_motion']
             and session['source_packet_sha256'] == state['source_packet_sha256'],
             'The retained Rigging draft changed during a failed worklist transaction.')
    enabled, role, tweak = session['enabled'], session['edit_role'], session['tweak_enabled']
    session['tracks'], session['properties_before'] = {}, {}
    session['enabled'], session['tweak_enabled'] = False, False
    _store(rig, session)
    if 'weapon_record' in state and state['status'] == 'detached':
        _return_weapon(rig, state)
    if enabled:
        activate(context, session, edit_role=role, enabled=tweak)
        _restore_live_pose(rig, state['live_prior'])
        session.update(state['live'])
        _store(rig, session)
    _consume_token(rig, state, 'restored')


def _cancel_reacquire():
    global _REACQUIRE_TIMER, _REACQUIRE_GENERATION
    _REACQUIRE_GENERATION += 1
    timer, _REACQUIRE_TIMER = _REACQUIRE_TIMER, None
    _REACQUIRE_PENDING.clear()
    if timer is not None and bpy.app.timers.is_registered(timer):
        bpy.app.timers.unregister(timer)


def _deferred_reacquire(generation):
    """One read-only retry after lifecycle evaluation settles; never poll."""
    global _REACQUIRE_TIMER
    if generation != _REACQUIRE_GENERATION:
        return None
    pending = tuple(_REACQUIRE_PENDING.items())
    _REACQUIRE_PENDING.clear()
    _REACQUIRE_TIMER = None
    for pointer, (name, payload) in pending:
        try:
            rig = bpy.data.objects.get(name)
            if (rig is None or rig.as_pointer() != pointer
                    or rig.get(SESSION_KEY) != payload):
                # A newer explicit operation owns this state now. The stale
                # admission may neither evict its cache nor replace its error.
                continue
            session = current(rig)
            _validate_preview_reference(session, files=True)
            _HANDLER_ERRORS.pop(pointer, None)
            if session['enabled']:
                _handlers()
        except Exception as error:
            _CACHE.pop(pointer, None)
            first = _HANDLER_ERRORS.get(pointer, 'Lifecycle admission failed.')
            _HANDLER_ERRORS[pointer] = first + '; deferred lifecycle admission: ' + str(error)
    _remove_handlers_if_idle()
    return None


@bpy.app.handlers.persistent
def _reacquire(_unused=None):
    # Fresh IDs after load/Undo/Redo are admitted read-only. Registration never
    # executes a solver, a stored script, or a frame evaluation. The first
    # owned seek/explicit Activate restores the committed preview inputs.
    global _REACQUIRE_TIMER
    _cancel_reacquire()
    _CACHE.clear()
    _BUSY.clear()
    _SUSPENDED.clear()
    _HANDLER_ERRORS.clear()
    _OWNED_TOKENS.clear()
    for rig in bpy.data.objects:
        if SESSION_KEY not in rig:
            continue
        try:
            session = current(rig)
            _validate_preview_reference(session, files=True)
            if session['enabled']:
                _handlers()
        except Exception as error:
            pointer = rig.as_pointer()
            _CACHE.pop(pointer, None)
            _HANDLER_ERRORS[pointer] = str(error)
            # Scalars only: the callback reacquires the current native ID and
            # repeats every identity, transform, NLA/Tweak and file check.
            payload = rig[SESSION_KEY]
            if isinstance(payload, str):
                _REACQUIRE_PENDING[pointer] = (rig.name, payload)
    _remove_handlers_if_idle()
    if _REACQUIRE_PENDING:
        generation = _REACQUIRE_GENERATION
        _REACQUIRE_TIMER = lambda generation=generation: _deferred_reacquire(generation)
        try:
            bpy.app.timers.register(_REACQUIRE_TIMER, first_interval=0., persistent=False)
        except Exception as error:
            for pointer in _REACQUIRE_PENDING:
                _HANDLER_ERRORS[pointer] += '; cannot schedule lifecycle admission: ' + str(error)
            _REACQUIRE_PENDING.clear()
            _REACQUIRE_TIMER = None


def _registration_reacquire(generation):
    """Admit fresh IDs once Blender leaves its restricted add-on registration."""
    global _REACQUIRE_TIMER
    if generation != _REACQUIRE_GENERATION:
        return None
    _REACQUIRE_TIMER = None
    _reacquire()
    return None


def register():
    global _REACQUIRE_TIMER
    # In-place reload replaces function objects. Remove only this module's old
    # registrations before admitting fresh IDs or installing its new callbacks.
    for collection, callback in ((bpy.app.handlers.load_post, _reacquire),
            (bpy.app.handlers.undo_post, _reacquire), (bpy.app.handlers.redo_post, _reacquire),
            (bpy.app.handlers.frame_change_post, _frame), (bpy.app.handlers.depsgraph_update_post, _depsgraph)):
        for previous in tuple(collection):
            if (previous is not callback and getattr(previous, '__module__', None) == __name__
                    and getattr(previous, '__name__', None) == callback.__name__):
                collection.remove(previous)
    for collection in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _reacquire not in collection:
            collection.append(_reacquire)
    if hasattr(bpy.data, 'objects'):
        _reacquire()
    else:
        # addon_utils.enable temporarily replaces bpy.data with _RestrictData.
        # Retain only a generation number; unregister/reload cancels this timer.
        _cancel_reacquire()
        generation = _REACQUIRE_GENERATION
        _REACQUIRE_TIMER = lambda generation=generation: _registration_reacquire(generation)
        try:
            bpy.app.timers.register(_REACQUIRE_TIMER, first_interval=0., persistent=False)
        except Exception:
            _REACQUIRE_TIMER = None
            raise


def unregister():
    # Persistent ownership and saved input Actions remain available for an
    # explicit reactivation. No artist state is changed during module unload.
    _cancel_reacquire()
    for collection, callback in ((bpy.app.handlers.load_post, _reacquire),
            (bpy.app.handlers.undo_post, _reacquire), (bpy.app.handlers.redo_post, _reacquire),
            (bpy.app.handlers.frame_change_post, _frame), (bpy.app.handlers.depsgraph_update_post, _depsgraph)):
        for previous in tuple(collection):
            if (previous is callback or (getattr(previous, '__module__', None) == __name__
                    and getattr(previous, '__name__', None) == callback.__name__)):
                collection.remove(previous)
    _CACHE.clear()
    _BUSY.clear()
    _SUSPENDED.clear()
    _HANDLER_ERRORS.clear()
    _OWNED_TOKENS.clear()
