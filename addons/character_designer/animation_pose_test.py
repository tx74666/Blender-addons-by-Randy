"""An owned, persistent Body-pose sandbox; never edit the previous Action.

Only the scratch Action and the rig's token own the session. No handlers or
process-local cache are needed after Undo, save or reload. A keyed scratch is
retained as an ordinary draft instead of being cleared by Reset or Finish.
"""

from contextlib import contextmanager
import hashlib
import json
import uuid

import bpy


TOKEN_KEY = 'character_designer_base_pose_test_v1'
NATURAL_KEY = 'character_designer_natural_pose_test_v1'
SESSION_KEY = 'character_designer_base_pose_test_session_v1'
REFS_KEY = 'character_designer_base_pose_test_refs_v1'
ARCHIVE_KEY = 'character_designer_base_pose_archive_v1'
ARCHIVE_REFS_KEY = 'character_designer_base_pose_archive_refs_v1'
_TRANSFORMS = ('location', 'rotation_euler', 'rotation_quaternion',
               'rotation_axis_angle', 'scale')
_OBJECT_TRANSFORMS = _TRANSFORMS + ('delta_location', 'delta_rotation_euler',
                                  'delta_rotation_quaternion', 'delta_scale')
_PROPERTIES = ('ik_fk', 'uniform_scale')
_ANIMATION = ('use_nla', 'action_blend_type', 'action_influence', 'action_extrapolation')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_encoded(value).encode('utf-8')).hexdigest()


def _pack(payload):
    return _encoded({'payload': payload, 'sha256': _digest(payload)})


def _unpack(raw):
    try:
        envelope = json.loads(raw)
        payload = envelope['payload']
        valid = (set(envelope) == {'payload', 'sha256'}
                 and envelope['sha256'] == _digest(payload)
                 and payload['version'] == 1
                 and uuid.UUID(hex=payload['token']).hex == payload['token']
                 and uuid.UUID(hex=payload['action_id']).hex == payload['action_id'])
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError('The Base Pose recovery record is invalid; it was preserved.') from exc
    _require(valid, 'The Base Pose recovery record changed; it was preserved.')
    return payload


def active(rig):
    """An invalid token still requires recovery, rather than starting over it."""
    return rig is not None and TOKEN_KEY in rig



def natural_active(rig):
    """Cheap UI state only; execution authenticates the persistent carrier."""
    try:
        if not isinstance(rig, bpy.types.Object) or rig.type != 'ARMATURE':
            return False
        token = rig.get(TOKEN_KEY)
        return isinstance(token, str) and rig.get(NATURAL_KEY) == token
    except (AttributeError, ReferenceError, TypeError):
        return False


def _natural_record(record):
    return record.get('mode', 'BASE') == 'NATURAL'


def _clear_natural(rig, record):
    if _natural_record(record) and rig.get(NATURAL_KEY) == record['token']:
        rig.pop(NATURAL_KEY, None)


def _rig(context):
    from . import animation_rest_pose
    return animation_rest_pose.get_rig(context)


def _pose_owner(rig):
    """Prove isolation for existing pose/display changes, never rig rebuilding.

    ID references from retained Actions do not evaluate another pose. A second
    actual Object binding does, including hidden or unlinked Objects. Old
    recovery records are kept as-is and gain no restoration permission here.
    """
    try:
        if (rig.type != 'ARMATURE' or rig.library or rig.data.library
                or rig.override_library or rig.data.override_library
                or not rig.is_editable or not rig.data.is_editable):
            return False
        bound = [obj for obj in bpy.data.objects
                 if obj.type == 'ARMATURE' and obj.data == rig.data]
        return len(bound) == 1 and bound[0] == rig
    except (AttributeError, ReferenceError, TypeError):
        return False


def _admission(context, rig, *, natural=False, _purpose='BASE_POSE'):
    from . import animation_composition, body_original_mode, forearm_twist
    from . import unity_animation
    _require(type(_purpose) is str and _purpose in {'BASE_POSE', 'ANIMATION'}
             and not (natural and _purpose == 'ANIMATION'),
             'Invalid animation operation context; the current state was kept.')
    operation = 'testing a Base Pose' if _purpose == 'BASE_POSE' else 'switching animations'
    subject = 'Base Pose' if _purpose == 'BASE_POSE' else 'Animation switching'
    if natural:
        from . import control_pose_blend_ui
        modal = getattr(getattr(context, 'window', None), 'modal_operators', None)
        _require(modal is None or modal.get('POSELIB_OT_blend_pose_asset') is None,
                 'Finish or cancel Blend Pose before starting the static Rest test.')
        for operator in tuple(control_pose_blend_ui._ACTIVE_OPERATORS):
            session = getattr(operator, '_session', None)
            live = (isinstance(session, dict) and session.get('rig') == rig
                    and session.get('closed') is not True)
            uncertain = (not isinstance(session, dict)
                         and getattr(operator, 'target_name', None) == getattr(rig, 'name', None))
            _require(not (live or uncertain),
                     'Finish or cancel Blend Pose before starting the static Rest test.')
    _require(context.mode in {'OBJECT', 'POSE'},
             'Finish the current Edit operation before ' + operation + '.')
    _require(rig.type == 'ARMATURE' and not rig.library and not rig.data.library
             and not rig.override_library and rig.is_editable and rig.data.is_editable,
             subject + ' needs one local, single-user Main Rig.')
    _require(_pose_owner(rig),
             'Another Object shares this Main Rig data; ' + subject + ' left it unchanged.')
    _require(context.scene.objects.get(rig.name) == rig
             and context.view_layer.objects.get(rig.name) == rig,
             ('Return to the Main Rig scene and view layer before testing a Pose.'
              if _purpose == 'BASE_POSE' else 'Return to the Main Rig scene and view layer before switching animations.'))
    _require(not rig.animation_data or not rig.animation_data.use_tweak_mode,
             'Exit NLA Tweak Mode before ' + operation + '; its strip was kept.')
    _require(not animation_composition.active(rig),
             'Close Composition before ' + operation + '; its drafts are kept.')
    _require(natural or not body_original_mode.active(rig),
             'Return from Original mode before ' + operation + '.')
    _require(unity_animation.active_preview(rig) is None,
             'Restore the Unity animation preview before ' + operation + '.')
    _require('character_designer_rigging_session_v1' not in rig,
             'Close the live Rigging input preview before ' + operation + '.')
    preview = getattr(forearm_twist, '_SESSION', None)
    _require(not preview or preview.get('armature') != rig,
             'Confirm or cancel Forearm calibration before ' + operation + '.')
    _require(not rig.hide_viewport and rig.visible_get(view_layer=context.view_layer),
             'Show the Main Rig in this view layer before ' + operation + '.')


def can_start(context):
    """Cheap UI admission only; execute authenticates the complete saved owner."""
    try:
        if context.mode not in {'OBJECT', 'POSE'}:
            return False
        main = getattr(getattr(context.scene, 'character_designer_setup', None), 'rig', None)
        work = getattr(getattr(context.scene, 'character_designer_animation_worklist', None), 'rig', None)
        if main is not None and work is not None and main != work:
            return False
        rig = main if main is not None else work
        return bool(rig is not None and rig.type == 'ARMATURE'
                    and not rig.library and not rig.data.library and not rig.override_library
                    and context.view_layer.objects.get(rig.name) == rig
                    and 'character_designer_composition_session_v1' not in rig
                    and 'character_designer_body_original_mode_v1' not in rig
                    and 'character_designer_rigging_session_v1' not in rig)
    except (AttributeError, ReferenceError, TypeError, KeyError):
        return False


def _structure(rig):
    return _digest({
        'data': rig.data.name_full,
        'bones': {b.name: [b.parent.name if b.parent else None, float(b.length),
                           [float(v) for row in b.matrix_local for v in row],
                           b.use_connect, b.inherit_scale, b.use_inherit_rotation,
                           b.use_local_location] for b in rig.data.bones},
        'constraints': {p.name: [[c.name, c.type,
                                 getattr(getattr(c, 'target', None), 'name_full', None),
                                 getattr(c, 'subtarget', None)] for c in p.constraints]
                        for p in rig.pose.bones}})


def _selection(bone):
    return bool((bone if hasattr(bone, 'select') else bone.bone).select)


def _capture(context, rig):
    from . import bone_display, limb_ik_fk, animation_rest_pose
    ad = rig.animation_data
    old_action = ad.action if ad else None
    old_slot = ad.action_slot if old_action else None
    _require(old_action is None or old_slot is not None,
             'The current Action has no unambiguous slot; its binding was kept.')
    refs = {'rig': rig}
    if old_action is not None:
        refs['previous_action'] = old_action
    if context.object is not None:
        refs['active_object'] = context.object
    for index, obj in enumerate(context.selected_objects):
        refs['selected_' + str(index)] = obj
    state = {
        'scene': context.scene.name_full, 'view_layer': context.view_layer.name,
        'had_animation': ad is not None, 'had_action': old_action is not None,
        'slot': old_slot.identifier if old_slot else None,
        'slot_handle': int(old_slot.handle) if old_slot else None,
        'animation': {name: getattr(ad, name) for name in _ANIMATION} if ad else {},
        'pose_position': rig.data.pose_position,
        'rest_marker': rig.get(animation_rest_pose.PREVIEW_KEY),
        'frame': int(context.scene.frame_current), 'subframe': float(context.scene.frame_subframe),
        'autokey': bool(context.scene.tool_settings.use_keyframe_insert_auto),
        'mode': context.object.mode if context.object else 'OBJECT',
        'selected_count': len(context.selected_objects),
        'reference_names': {name: value.name_full for name, value in refs.items()},
        'active_bone': rig.data.bones.active.name if rig.data.bones.active else None,
        'bone_selection': {pb.name: _selection(pb) for pb in rig.pose.bones},
        'bone_collections': {bone.name: [collection.name for collection in bone.collections]
                             for bone in rig.data.bones},
        'pose': {pb.name: {'rotation_mode': pb.rotation_mode,
                           'channels': {name: list(getattr(pb, name)) for name in _TRANSFORMS},
                           'properties': {name: pb[name] for name in _PROPERTIES if name in pb}}
                 for pb in rig.pose.bones},
        'constraints': {pb.name: {con.name: {'mute': bool(con.mute),
                                           **({'pole_angle': float(con.pole_angle)}
                                              if con.type == 'IK' else {})}
                                  for con in pb.constraints} for pb in rig.pose.bones},
        'pole_metadata': limb_ik_fk._leg_pole_metadata(rig),
        'display': bone_display._snapshot(rig),
    }
    # Check serializability before any Action, mode or pose change.
    _encoded(state)
    return state, refs



def _natural_original(rig, state=None, refs=None):
    """Authenticate opaque Original ownership; never restore or rewrite it."""
    from . import body_original_mode as original, forearm_original_inventory
    present = original.active(rig)
    raw = rig.get(original.SESSION)
    has_refs = original.DISPLAY_REFS in rig
    display_refs = dict(rig.get(original.DISPLAY_REFS, {}))
    if present:
        _require(isinstance(raw, str), 'Natural Pose found invalid Original recovery data.')
        original._validate_session_rest(rig)
        forearm_original_inventory.validate(rig)
    else:
        _require(not has_refs, 'Original display recovery is orphaned; it was kept.')
    for target in display_refs.values():
        _require(isinstance(target, bpy.types.Object) and target.get(original.DISPLAY_OWNER) == rig,
                 'An Original display reference changed; its recovery was kept.')
    if state is None:
        return present, raw, has_refs, display_refs
    saved = state['natural_original']
    _require(present == saved['active'] and raw == saved['session_raw']
             and has_refs == saved['refs_present'] and list(display_refs) == saved['refs_keys'],
             'Original SESSION/REFS changed during Natural Pose; all recovery was kept.')
    _require(all(display_refs[name] == refs[key] for name, key in saved['refs'].items()),
             'An Original native reference changed during Natural Pose; recovery was kept.')


def _capture_natural(context, rig):
    from . import natural_dress_pose
    state, refs = _capture(context, rig)
    present, raw, has_refs, display_refs = _natural_original(rig)
    mapping = {}
    for index, (name, target) in enumerate(display_refs.items()):
        key = 'natural_original_' + str(index)
        refs[key] = target
        mapping[name] = key
    state['natural_original'] = dict(active=present, session_raw=raw,
        refs_present=has_refs, refs_keys=list(display_refs), refs=mapping)
    state['natural_object'] = {'rotation_mode': rig.rotation_mode,
        'channels': {name: list(getattr(rig, name)) for name in _OBJECT_TRANSFORMS}}
    _validate_natural_object(state)
    natural_dress_pose.capture(context, rig, state, refs)
    state['reference_names'] = {name: value.name_full for name, value in refs.items()}
    _encoded(state)
    return state, refs



def _validate_natural_object(state):
    saved = state['natural_object']
    _require(isinstance(saved, dict) and set(saved) == {'rotation_mode', 'channels'}
             and saved['rotation_mode'] in {'QUATERNION', 'AXIS_ANGLE', 'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
             and isinstance(saved['channels'], dict) and set(saved['channels']) == set(_OBJECT_TRANSFORMS),
             'The Natural Pose Object input snapshot changed; recovery was kept.')
    for name, values in saved['channels'].items():
        count = 4 if name in {'rotation_quaternion', 'rotation_axis_angle', 'delta_rotation_quaternion'} else 3
        _require(isinstance(values, (list, tuple)) and len(values) == count
                 and all(type(value) in {int, float} for value in values),
                 'A Natural Pose Object input field is invalid; recovery was kept.')
    _encoded(saved)


def _restore_natural_object(rig, state):
    saved = state['natural_object']
    rig.rotation_mode = saved['rotation_mode']
    for name in _OBJECT_TRANSFORMS:
        setattr(rig, name, saved['channels'][name])


def _capture_mode(context, rig, record):
    return _capture_natural(context, rig) if _natural_record(record) else _capture(context, rig)


def _object_mode(context):
    if context.object is not None and context.object.mode != 'OBJECT':
        result = bpy.ops.object.mode_set(mode='OBJECT')
        _require('FINISHED' in result, 'Blender could not finish the current Pose Mode.')


def _enter_pose(context, rig):
    _object_mode(context)
    for obj in tuple(context.selected_objects):
        obj.select_set(False)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    result = bpy.ops.object.mode_set(mode='POSE')
    _require('FINISHED' in result, 'Blender could not enter Main Rig Pose Mode.')


def _slot(action, identifier, handle):
    matches = [slot for slot in action.slots
               if slot.identifier == identifier and int(slot.handle) == handle
               and slot.target_id_type == 'OBJECT']
    _require(len(matches) == 1, 'The saved Action slot changed; recovery data was kept.')
    return matches[0]


def _validate_restore(context, rig, state, refs):
    if 'natural_original' in state:
        from . import natural_dress_pose
        _natural_original(rig, state, refs)
        _validate_natural_object(state)
        natural_dress_pose.validate(context, rig, state, refs)
    _require(context.scene.name_full == state['scene']
             and context.view_layer.name == state['view_layer'],
             'Return to the scene and view layer where Base Pose was started.')
    _require(refs.get('rig') == rig, 'The Base Pose recovery belongs to another rig.')
    _require(set(refs) == set(state['reference_names'])
             and all(refs[name] is not None and refs[name].name_full == expected
                     for name, expected in state['reference_names'].items()),
             'A saved Base Pose ID reference was renamed or reassigned; recovery was kept.')
    _require(set(state['pose']) == set(rig.pose.bones.keys()),
             'The rig bones changed; Base Pose recovery was kept.')
    for key in ('active_object', *('selected_' + str(i) for i in range(state['selected_count']))):
        obj = refs.get(key)
        if key == 'active_object' and obj is None:
            continue
        _require(obj is not None and context.view_layer.objects.get(obj.name) == obj,
                 'An original selection object is unavailable; recovery was kept.')
    previous = refs.get('previous_action')
    _require((previous is not None) == state['had_action'],
             'The previous Action reference changed; recovery was kept.')
    if previous is not None:
        _slot(previous, state['slot'], state['slot_handle'])
    for name, constraints in state['constraints'].items():
        pb = rig.pose.bones[name]
        _require(set(constraints) == set(pb.constraints.keys()),
                 'Rig constraints changed; recovery was kept.')
    _require(all(rig.data.collections_all.get(name) is not None
                 for names in state['bone_collections'].values() for name in names),
             'An original Bone Collection was renamed or removed; recovery was kept.')


def _restore(context, rig, state, refs):
    # ToolSettings is not part of native Undo. Never persistently change the
    # artist's current AutoKey choice; automatic insertion is suspended only
    # while restoring raw channels and the Action binding.
    autokey = context.scene.tool_settings.use_keyframe_insert_auto
    context.scene.tool_settings.use_keyframe_insert_auto = False
    try:
        _restore_disabled(context, rig, state, refs)
    finally:
        context.scene.tool_settings.use_keyframe_insert_auto = autokey


def _restore_disabled(context, rig, state, refs):
    from . import animation_rest_pose, bone_display, limb_ik_fk
    _validate_restore(context, rig, state, refs)
    _object_mode(context)
    context.scene.tool_settings.use_keyframe_insert_auto = False
    previous = refs.get('previous_action')
    ad = rig.animation_data_create()
    ad.action = previous
    if previous is not None:
        ad.action_slot = _slot(previous, state['slot'], state['slot_handle'])
    for name, value in state['animation'].items():
        setattr(ad, name, value)
    rig.data.pose_position = state['pose_position']
    if state['rest_marker'] is None:
        rig.pop(animation_rest_pose.PREVIEW_KEY, None)
    else:
        rig[animation_rest_pose.PREVIEW_KEY] = state['rest_marker']
    context.scene.frame_set(state['frame'], subframe=state['subframe'])
    if 'natural_object' in state:
        _restore_natural_object(rig, state)
    for name, saved in state['pose'].items():
        pb = rig.pose.bones[name]
        pb.rotation_mode = saved['rotation_mode']
        for prop, values in saved['channels'].items():
            setattr(pb, prop, values)
        for prop in _PROPERTIES:
            if prop in saved['properties']:
                pb[prop] = saved['properties'][prop]
            elif prop in pb:
                del pb[prop]
    for name, constraints in state['constraints'].items():
        for con_name, saved in constraints.items():
            con = rig.pose.bones[name].constraints[con_name]
            con.mute = saved['mute']
            if 'pole_angle' in saved:
                con.pole_angle = saved['pole_angle']
    limb_ik_fk._restore_leg_pole_metadata(rig, state['pole_metadata'])
    context.view_layer.update()
    bone_display._restore(rig, state['display'])
    # Display matching can reassign an existing Body membership, changing only
    # its order alongside _Internal. Preserve the original ordered membership,
    # without replacing Collections or accepting a different membership set.
    for name, expected in state['bone_collections'].items():
        bone = rig.data.bones[name]
        current = [collection.name for collection in bone.collections]
        _require(set(current) == set(expected),
                 'Bone Collection membership changed; Base Pose recovery was kept.')
        if current != expected:
            for collection in tuple(bone.collections):
                collection.unassign(bone)
            for collection in expected:
                rig.data.collections_all[collection].assign(bone)
            _require([collection.name for collection in bone.collections] == expected,
                     'Blender could not restore Bone Collection order; recovery was kept.')
    for pb in rig.pose.bones:
        (pb if hasattr(pb, 'select') else pb.bone).select = state['bone_selection'][pb.name]
    rig.data.bones.active = rig.data.bones.get(state['active_bone'] or '')
    for obj in tuple(context.selected_objects):
        obj.select_set(False)
    for index in range(state['selected_count']):
        refs['selected_' + str(index)].select_set(True)
    context.view_layer.objects.active = refs.get('active_object')
    if state['mode'] == 'POSE':
        result = bpy.ops.object.mode_set(mode='POSE')
        _require('FINISHED' in result, 'Blender could not restore the original Pose Mode.')
    if not state['had_animation'] and not ad.drivers and not ad.nla_tracks and ad.action is None:
        rig.animation_data_clear()


def _has_content(action):
    """An FCurve is authored content even when all its key points were removed."""
    if len(getattr(action, 'pose_markers', ())):
        return True
    if any(key not in {SESSION_KEY, REFS_KEY} for key in action.keys()):
        return True
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type != 'KEYFRAME':
                return True
            for bag in strip.channelbags:
                if len(bag.fcurves):
                    return True
    return bool(getattr(action, 'fcurves', ())) if not action.is_action_layered else False


def _session_action(rig):
    """Find the authenticated carrier, without authorizing any restoration."""
    token = rig.get(TOKEN_KEY)
    _require(isinstance(token, str), 'The Base Pose token is invalid; recovery was kept.')
    candidates = []
    for action in bpy.data.actions:
        if SESSION_KEY not in action:
            continue
        refs = action.get(REFS_KEY, {})
        if refs.get('rig') != rig:
            continue
        record = _unpack(action[SESSION_KEY])
        if record['token'] == token:
            candidates.append((action, record, refs))
    _require(len(candidates) == 1, 'The Base Pose owner is missing or ambiguous; all drafts were kept.')
    return candidates[0]


def _record(context, rig):
    action, record, refs = _session_action(rig)
    return _validate_record(context, rig, action, record, refs)


def _natural_matching_record(context, rig):
    """Authenticate the actual bound carrier during a synchronous keyed Reset.

    Its prior authored carrier still exists until matching succeeds. Ordinary
    operations retain _record's unique-carrier scan and recovery admission.
    """
    ad = rig.animation_data
    action = ad.action if ad else None
    _require(natural_active(rig) and action is not None and SESSION_KEY in action
             and REFS_KEY in action, 'Natural neutral matching has no current owned carrier.')
    record, refs = _unpack(action[SESSION_KEY]), action[REFS_KEY]
    _require(_natural_record(record) and record['token'] == rig.get(TOKEN_KEY),
             'The current Action does not own Natural neutral matching.')
    return _validate_record(context, rig, action, record, refs)


def _validate_record(context, rig, action, record, refs):
    mode = record.get('mode', 'BASE')
    _require(mode in {'BASE', 'NATURAL'}, 'Unknown Pose test mode; recovery was kept.')
    _require(mode != 'NATURAL' or isinstance(record.get('before', {}).get('natural_original'), dict),
             'The Natural Pose owner snapshot is missing; recovery was kept.')
    _require(mode != 'NATURAL' or isinstance(record.get('before', {}).get('natural_object'), dict),
             'The Natural Pose Object input snapshot is missing; recovery was kept.')
    _require((rig.get(NATURAL_KEY) == record['token']) if mode == 'NATURAL'
             else NATURAL_KEY not in rig, 'The Natural Pose marker changed; recovery was kept.')
    _require(_structure(rig) == record['structure'],
             'The rig Rest or control graph changed; Base Pose recovery was kept.')
    _require(len(action.slots) == 1, 'The test Action has foreign slots; it was kept.')
    slot = _slot(action, record['slot'], record['slot_handle'])
    ad = rig.animation_data
    _require(ad is not None and ad.action == action and ad.action_slot == slot,
             'The active Action binding changed; the test draft and recovery were kept.')
    _require(action.users - int(action.use_fake_user) == 1,
             'Another user now owns the test Action; its draft and recovery were kept.')
    _require(not action.library and not action.asset_data,
             'The test Action became linked or an asset; recovery was kept.')
    _validate_restore(context, rig, record['before'], refs)
    return action, record, refs


def _reference_names(refs):
    return {key: value.name_full for key, value in refs.items() if value is not None}


def read_archive(archive):
    """Read a preserved checkpoint as data; this never grants restore rights."""
    payload = _unpack(archive[ARCHIVE_KEY])
    _require(payload.get('kind') == 'PRESERVED_BASE_POSE',
             'This is not a preserved Base Pose checkpoint.')
    original = _unpack(payload['original_session'])
    refs = archive[ARCHIVE_REFS_KEY]
    original_refs, current_refs = dict(refs['original']), dict(refs['current'])
    draft = refs['draft']
    _require(draft is not None and refs['rig'] == original_refs.get('rig')
             and refs['rig'] == current_refs.get('rig'),
             'A preserved Base Pose ID reference is unavailable; the archive was kept.')
    _require(original['token'] == payload['token']
             and original['action_id'] == payload['original_action_id'],
             'The preserved Base Pose checkpoint changed; the archive was kept.')
    # The archive is a different Action. Reuse its one draft reference instead
    # of recording the old draft twice (or a self-reference on that draft).
    _require(payload['current_action'] == 'ORIGINAL_DRAFT'
             and 'previous_action' not in current_refs,
             'The preserved current binding is invalid; the archive was kept.')
    current_refs['previous_action'] = draft
    _require(_reference_names(original_refs) == original['before']['reference_names']
             and _reference_names(current_refs) == payload['current']['reference_names'],
             'A preserved Base Pose reference was renamed; the archive was kept.')
    return {'original': original, 'original_refs': original_refs,
            'current': payload['current'], 'current_refs': current_refs,
            'current_structure': payload['current_structure'], 'draft': draft,
            'restoration_permission': False}


def keep_stale_draft(context):
    """End an obsolete, still-bound test, keeping its record and current pose.

    This deliberately does not restore the previous animation. The ordinary
    worklist must still authenticate any animation selected on the current rig.
    """
    rig = _rig(context)
    _admission(context, rig, natural=natural_active(rig))
    action, record, refs = _session_action(rig)
    structure = _structure(rig)
    _require(structure != record['structure'],
             'This Base Pose checkpoint still matches the rig; use Return to Animation.')
    _require(not action.library and not action.asset_data and len(action.slots) == 1,
             'The test Action is linked, shared or has foreign slots; its recovery was kept.')
    slot = _slot(action, record['slot'], record['slot_handle'])
    ad = rig.animation_data
    _require(ad is not None and ad.action == action and ad.action_slot == slot,
             'The current test binding changed; its draft and recovery were kept.')
    _require(action.users - int(action.use_fake_user) == 1,
             'Another user now owns the test Action; its draft and recovery were kept.')
    before = record['before']
    _require(context.scene.name_full == before['scene']
             and context.view_layer.name == before['view_layer'],
             'Return to the Base Pose scene and view layer before keeping this draft.')
    _require(set(refs) == set(before['reference_names'])
             and _reference_names(refs) == before['reference_names'],
             'A saved Base Pose ID reference changed; its recovery was kept.')
    previous = refs.get('previous_action')
    _require((previous is not None) == before['had_action'],
             'The previous Action reference changed; its recovery was kept.')
    if previous is not None:
        _slot(previous, before['slot'], before['slot_handle'])

    current, current_refs = _capture_mode(context, rig, record)
    original_session = action[SESSION_KEY]
    token = rig[TOKEN_KEY]
    preserved_refs = dict(current_refs)
    _require(preserved_refs.pop('previous_action', None) == action,
             'The current draft binding could not be captured; recovery was kept.')
    payload = {'version': 1, 'kind': 'PRESERVED_BASE_POSE', 'token': record['token'],
               'action_id': uuid.uuid4().hex, 'original_action_id': record['action_id'],
               'original_session': original_session, 'current': current,
               'current_structure': structure, 'current_action': 'ORIGINAL_DRAFT'}
    encoded = _pack(payload)
    archive = None
    try:
        archive = bpy.data.actions.new('Base Pose Recovery')
        archive[ARCHIVE_KEY] = encoded
        archive[ARCHIVE_REFS_KEY] = {'rig': rig, 'draft': action,
                                   'original': dict(refs), 'current': preserved_refs}
        archive.use_fake_user = True
        saved = read_archive(archive)
        _require(archive[ARCHIVE_KEY] == encoded and saved['original'] == record
                 and saved['original_refs'] == dict(refs)
                 and saved['current_refs'] == current_refs
                 and saved['current'] == current and saved['current_structure'] == structure
                 and saved['draft'] == action and action[SESSION_KEY] == original_session
                 and dict(action[REFS_KEY]) == dict(refs)
                 and rig.get(TOKEN_KEY) == token and _structure(rig) == structure,
                 'The preserved checkpoint did not read back exactly; the test was kept.')
        # Only the current empty/draft binding is released. No historical
        # channels, constraints, pole data, display, Rest or Action are applied.
        ad.action = None
        rig.pop(TOKEN_KEY)
        _clear_natural(rig, record)
    except Exception:
        rig[TOKEN_KEY] = token
        if _natural_record(record):
            rig[NATURAL_KEY] = token
        ad.action = action
        ad.action_slot = slot
        if archive is not None:
            archive.use_fake_user = False
            if archive.users == 0:
                bpy.data.actions.remove(archive)
            else:
                archive.use_fake_user = True
        raise
    return {'rig': rig, 'draft': action, 'archive': archive,
            'animation_restored': False}


def _new_action(rig, record, refs):
    action = bpy.data.actions.new('Natural Pose Test' if _natural_record(record) else 'Base Pose Test')
    try:
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        payload = {**record, 'action_id': uuid.uuid4().hex,
                   'slot': slot.identifier, 'slot_handle': int(slot.handle)}
        action[SESSION_KEY] = _pack(payload)
        action[REFS_KEY] = dict(refs)
        return action, payload
    except Exception:
        if action.users == 0:
            bpy.data.actions.remove(action)
        raise


def _retire(action):
    """Never destroy authored test curves, even on a successful Finish."""
    authored = _has_content(action)
    _require(action.users - int(action.use_fake_user) == 0,
             'The detached test Action gained a foreign user; recovery was kept.')
    if authored:
        action.use_fake_user = True
        action.name = 'Pose Test Draft'
    action.pop(SESSION_KEY, None)
    action.pop(REFS_KEY, None)
    if not authored:
        action.use_fake_user = False
        if action.users == 0:
            bpy.data.actions.remove(action)
    return action if authored else None


def _neutral(context, rig, *, _natural=False):
    from . import control_pose_assets as poses, control_pose_capture as capture
    from . import forearm_original_inventory, limb_ik_fk, limb_ik
    controls = limb_ik._validate_inventory(rig)
    for entry in controls['rigs'].values():
        target = rig.pose.bones[entry['target'].name]
        _require(target.bone.get(limb_ik_fk.VERSION_KEY) == limb_ik_fk.VERSION,
                 'Update this limb control graph before testing Base Pose.')
        for pb, constraint, record in entry['entries']:
            if limb_ik_fk.is_switch_constraint(rig, pb, constraint, record):
                limb_ik_fk.validate_constraint_influence(rig, pb, constraint, record)
    inventory = forearm_original_inventory.validate(rig)
    names = capture._resolve_names(rig, inventory, 'ALL_BODY', True)
    capture._validate_channels(rig, names, inventory)
    values = {name: {'location': dict(enumerate((0., 0., 0.))),
                     'rotation_quaternion': dict(enumerate((1., 0., 0., 0.))),
                     'scale': dict(enumerate((1., 1., 1.)))} for name in names}
    metadata = {'version': 1, 'source_object': rig.name, 'source_slot': 'OB' + rig.name,
                'rest': poses.native_rest(rig), 'scope': 'ALL_BODY',
                'names': names, 'include_fingers': True}
    desired = poses.desired_pose(rig, values, metadata)
    for name in names:
        _require(poses._difference(desired[name], rig.data.bones[name].matrix_local) <= 2e-6,
                 'This Body hierarchy cannot represent native Base Pose without changing its Rest.')
    if _natural:
        poses._apply_natural_neutral(context, rig, values, metadata=metadata)
    else:
        poses.apply_channels(context, rig, values, metadata=metadata, _pose_only_owner=True)
    limb_ik_fk._update(context, rig)
    for name in names:
        _require(poses._difference(rig.pose.bones[name].matrix, desired[name]) <= 4e-4,
                 'Blender could not match the exact Base Pose; the attempt was rolled back.')


def start_or_reset(context):
    """Enter editable Pose display or reset it; return any retained keyed draft."""
    from . import animation_rest_pose, unity_animation
    rig = _rig(context)
    _admission(context, rig)
    repeated = active(rig)
    previous = record = refs = None
    if repeated:
        previous, record, refs = _record(context, rig)
        _require(not _natural_record(record), 'Finish Natural Pose before starting a Base Pose test.')
    attempt, attempt_refs = _capture(context, rig)
    autokey = context.scene.tool_settings.use_keyframe_insert_auto
    if not repeated:
        record = {'version': 1, 'token': uuid.uuid4().hex,
                  'structure': _structure(rig), 'before': attempt}
        refs = attempt_refs
    fresh = previous is None or _has_content(previous)
    action = previous
    try:
        unity_animation._set_playing(context, False)
        if fresh:
            action, record = _new_action(rig, record, refs)
        context.scene.tool_settings.use_keyframe_insert_auto = False
        ad = rig.animation_data_create()
        ad.action = action
        ad.action_slot = _slot(action, record['slot'], record['slot_handle'])
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.0
        ad.action_extrapolation = 'HOLD'
        rig.data.pose_position = 'POSE'
        rig.pop(animation_rest_pose.PREVIEW_KEY, None)
        _enter_pose(context, rig)
        context.view_layer.update()
        _neutral(context, rig)
        rig[TOKEN_KEY] = record['token']
    except Exception:
        _restore(context, rig, attempt, attempt_refs)
        if fresh and action is not None and action != previous:
            _retire(action)
        if not repeated:
            rig.pop(TOKEN_KEY, None)
        raise
    finally:
        context.scene.tool_settings.use_keyframe_insert_auto = autokey
    draft = _retire(previous) if fresh and previous is not None else None
    return {'rig': rig, 'reset': repeated, 'draft': draft}



def _natural_plan(rig):
    """Read-only full-rig admission; unrecognized evaluators stay a refusal."""
    from . import control_pose_capture as capture, forearm_original_inventory, root_control, natural_dress_pose, foot_controls
    inventory = forearm_original_inventory.validate(rig)
    owned = capture._owned_constraints(rig, inventory)
    owned.update(natural_dress_pose.plan(bpy.context, rig)['constraint_keys'])
    for pb in rig.pose.bones:
        _require(all((pb.name, con.name) in owned for con in pb.constraints),
                 pb.name + ': a foreign constraint prevents a Natural Pose; it was kept.')
    root = root_control.get_record(rig)
    own_scale = None
    if root:
        master = rig.pose.bones[root['master']]
        root_control._validate_scale_drivers(rig, master)
        own_scale = master.path_from_id('scale')
    # The inventory above already proves each Foot extension and its exact
    # driver expressions, indices and native targets (including Original's
    # saved mute view). These generated roll/bank drivers remain running;
    # the ordinary zero input and exact Rest check still govern their output.
    owned_drivers = {(own_scale, index) for index in range(3)} if own_scale else set()
    foot_drivers = {(entry['path'], entry['index'])
                    for record in foot_controls.records(rig).values()
                    for entry in record['drivers']}
    paths = {pb.path_from_id(prop) for pb in rig.pose.bones
             for prop in (*_TRANSFORMS, 'rotation_mode', 'matrix_basis')}
    paths.update((*_OBJECT_TRANSFORMS, 'rotation_mode', 'matrix_basis', 'matrix_local', 'matrix_world'))
    for curve in getattr(rig.animation_data, 'drivers', ()):
        key = (curve.data_path, curve.array_index)
        if key in foot_drivers:
            # Expression/target ownership does not prove the FCurve mapping.
            # Only the generated two-point identity evaluator may be admitted.
            points = curve.keyframe_points
            _require(len(curve.sampled_points) == 0 and len(points) == 2
                     and curve.extrapolation == 'LINEAR' and not curve.driver.use_self
                     and all(tuple(point.co) == expected and point.interpolation == 'LINEAR'
                             for point, expected in zip(points, ((0., 0.), (1., 1.)))),
                     'The generated Foot driver identity mapping changed; Natural Pose left it unchanged.')
            owned_drivers.add(key)
        _require(curve.data_path not in paths or key in owned_drivers,
                 'A transform driver controls this rig; Natural Pose left it unchanged.')
    _require(all(bone.bbone_segments == 1 for bone in rig.data.bones if bone.use_deform),
             'Natural Pose cannot neutralize segmented Bendy Bone shape channels safely.')
    return root


def _neutral_natural(context, rig, root):
    """Natural Pose is zero native pose, never a changed skeleton Rest."""
    from . import body_original_mode as original, control_pose_assets as poses, limb_ik_fk, natural_dress_pose
    # Only the authenticated bound scratch may pause these exact generated
    # Spline IK constraints. The original mutes live in before.constraints and
    # are restored by the existing Return/rollback path. DEF constraints keep
    # their Original/Controls settings and ownership throughout the test.
    _natural_matching_record(context, rig)
    dress = natural_dress_pose.plan(context, rig)
    for bone, constraint in dress['splines']:
        rig.pose.bones[bone].constraints[constraint].mute = True
    for pb in rig.pose.bones:
        pb.location = (0., 0., 0.)
        pb.rotation_euler = (0., 0., 0.)
        pb.rotation_quaternion = (1., 0., 0., 0.)
        pb.rotation_axis_angle = (0., 0., 1., 0.)
        pb.scale = (1., 1., 1.)
    if root:
        from . import root_control
        rig.pose.bones[root['master']][root_control.SCALE_PROPERTY] = 1.0
    limb_ik_fk._update(context, rig)
    if not original.active(rig):
        # Preserve the approved Controls matching path; the Original path must
        # never enter apply_channels/_match_original or rewrite its SESSION.
        _neutral(context, rig, _natural=True)
    limb_ik_fk._update(context, rig)
    for bone in rig.data.bones:
        if bone.use_deform:
            _require(poses._difference(rig.pose.bones[bone.name].matrix, bone.matrix_local) <= 4e-4,
                     bone.name + ': Blender could not reach the natural Rest; the attempt was rolled back.')


def start_natural(context):
    """Start/reset editable neutral posing, isolated from the retained animation."""
    from . import animation_rest_pose, unity_animation
    rig = _rig(context)
    _admission(context, rig, natural=True)
    repeated = active(rig)
    previous = record = refs = None
    if repeated:
        previous, record, refs = _record(context, rig)
        _require(_natural_record(record), 'Finish the Base Pose test before starting Natural Pose.')
    else:
        _require(NATURAL_KEY not in rig, 'Orphaned Natural Pose recovery was kept.')
    root = _natural_plan(rig)
    attempt, attempt_refs = _capture_natural(context, rig)
    autokey = context.scene.tool_settings.use_keyframe_insert_auto
    if not repeated:
        record = {'version': 1, 'mode': 'NATURAL', 'token': uuid.uuid4().hex,
                  'structure': _structure(rig), 'before': attempt}
        refs = attempt_refs
    fresh = previous is None or _has_content(previous)
    action = previous
    try:
        unity_animation._set_playing(context, False)
        if fresh:
            action, record = _new_action(rig, record, refs)
        context.scene.tool_settings.use_keyframe_insert_auto = False
        ad = rig.animation_data_create()
        ad.action = action
        ad.action_slot = _slot(action, record['slot'], record['slot_handle'])
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.0
        ad.action_extrapolation = 'HOLD'
        rig.data.pose_position = 'POSE'
        rig.pop(animation_rest_pose.PREVIEW_KEY, None)
        _restore_natural_object(rig, attempt)
        _enter_pose(context, rig)
        context.view_layer.update()
        rig[TOKEN_KEY] = record['token']
        rig[NATURAL_KEY] = record['token']
        _neutral_natural(context, rig, root)
        _natural_original(rig, attempt, attempt_refs)
    except Exception:
        _restore(context, rig, attempt, attempt_refs)
        if fresh and action is not None and action != previous:
            _retire(action)
        if not repeated:
            rig.pop(TOKEN_KEY, None)
            _clear_natural(rig, record)
        raise
    finally:
        context.scene.tool_settings.use_keyframe_insert_auto = autokey
    draft = _retire(previous) if fresh and previous is not None else None
    return {'rig': rig, 'reset': repeated, 'draft': draft, 'natural': True}


def finish(context):
    """Restore the first entry checkpoint; retain a keyed test as a local draft."""
    rig = _rig(context)
    _admission(context, rig, natural=natural_active(rig))
    if not active(rig):
        return {'rig': rig, 'draft': None, 'changed': False}
    action, record, refs = _record(context, rig)
    attempt, attempt_refs = _capture_mode(context, rig, record)
    try:
        _restore(context, rig, record['before'], refs)
    except Exception:
        _restore(context, rig, attempt, attempt_refs)
        raise
    draft = _retire(action)
    rig.pop(TOKEN_KEY, None)
    _clear_natural(rig, record)
    return {'rig': rig, 'draft': draft, 'changed': True}


@contextmanager
def suspended_for_operation(context, rig):
    """Temporarily restore the original binding; commit exit only on success.

    An operation that rejects restores the exact test pose/draft instead. Its
    caller remains responsible for rolling back its own unrelated mutations.
    """
    if not active(rig):
        yield
        return
    _admission(context, rig, natural=natural_active(rig))
    action, record, refs = _record(context, rig)
    attempt, attempt_refs = _capture_mode(context, rig, record)
    try:
        _restore(context, rig, record['before'], refs)
        _clear_natural(rig, record)
        yield
    except Exception:
        if _natural_record(record):
            rig[NATURAL_KEY] = record['token']
        _restore(context, rig, attempt, attempt_refs)
        raise
    else:
        _retire(action)
        rig.pop(TOKEN_KEY, None)
        _clear_natural(rig, record)
