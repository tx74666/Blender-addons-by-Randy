"""Explicit, copied-Action native-FK pose offsets; no playback callbacks.

Only selected transform channels are resampled (integer frames plus original
key times). Other curves and all original Actions stay untouched. Subframe
Bezier equivalence is intentionally not claimed for the affected channels.
"""
import ast
import hashlib
import json
import math
import re
from types import SimpleNamespace
import uuid

RECORD = 'character_designer_pose_offset_v1'
KEYED_RECORD = 'character_designer_pose_keyed_variant_v1'
_PATH = re.compile(r'^pose\.bones\[("(?:[^"\\]|\\.)*")\]\.(location|rotation_euler|rotation_quaternion|rotation_axis_angle|scale)$')
_TRANSFORMS = {'location': 3, 'rotation_euler': 3, 'rotation_quaternion': 4, 'scale': 3}
_MAX_FRAMES = 20001
_MAX_POINTS = 500000


def _require(value, message):
    if not value:
        raise ValueError(message)


def _modules():
    import bpy
    from . import control_pose_assets as poses, animation_worklist as worklist
    from . import animation_pose_test as pose_test, animation_rest_pose as rest
    from . import body_original_mode as original, animation_composition as composition
    from . import animation_worklist_revision as revision, animation_worklist_fingerprint as fingerprint
    from . import limb_ik, limb_ik_fk, unity_animation, animation_worklist_target, body_setup
    return SimpleNamespace(bpy=bpy, poses=poses, worklist=worklist, pose=pose_test,
        rest=rest, original=original, composition=composition, revision=revision,
        fingerprint=fingerprint, limb=limb_ik, match=limb_ik_fk, ua=unity_animation,
        target=animation_worklist_target, setup=body_setup)


def _factor(value):
    _require(type(value) in {float, int} and math.isfinite(value) and 0 <= value <= 1,
             'Pose Offset strength must be between 0 and 1.')
    return float(value)


def _frames(start, end, keyed):
    _require(all(math.isfinite(v) for v in (start, end)) and end >= start,
             'The animation has an invalid frame range.')
    _require(math.ceil(end) - math.floor(start) <= _MAX_FRAMES,
             'This animation is too long for one Pose Offset operation.')
    result = {float(start), float(end)}
    result.update(float(frame) for frame in range(math.ceil(start), math.floor(end) + 1))
    for value in keyed:
        _require(math.isfinite(value), 'The animation contains a non-finite key time.')
        _require(start <= value <= end, 'An authored key is outside this Action range; keep it before offsetting.')
        result.add(float(value))
    _require(len(result) <= _MAX_FRAMES, 'This animation has too many distinct Pose Offset samples.')
    return sorted(result)


def _curve_name(curve):
    found = _PATH.fullmatch(curve.data_path)
    return (ast.literal_eval(found[1]), found[2], int(curve.array_index)) if found else None


def _bag(action, handle):
    _require(getattr(action, 'is_action_layered', False),
             'Pose Offset needs a layered Action with an explicit Object slot.')
    bags = []
    for layer in action.layers:
        for strip in layer.strips:
            _require(strip.type == 'KEYFRAME', 'Unsupported Action strip; no animation was changed.')
            for bag in strip.channelbags:
                if bag.slot_handle == handle:
                    _require(getattr(layer, 'influence', 1.0) == 1.0,
                             'Weighted Action layers need a separate offset workflow.')
                    bags.append(bag)
    _require(len(bags) == 1, 'Pose Offset supports one unambiguous keyframe channelbag for this slot.')
    return bags[0]


def _action_hash(api, action, handle):
    proof = api.fingerprint.fingerprint_action(action, handle)
    _require(proof.get('fingerprint_known') is True,
             'The complete Action cannot be verified: ' + proof.get('proof_reason', 'unknown'))
    return proof['action_fingerprint']


def _constant_zero(curve):
    if curve.modifiers or getattr(curve, 'sampled_points', ()):
        return False
    return all(abs(float(point.co[1])) <= 1e-8 and
               (point.interpolation != 'BEZIER' or
                abs(float(point.handle_left[1])) <= 1e-8 and abs(float(point.handle_right[1])) <= 1e-8)
               for point in curve.keyframe_points)


def _native_fk(api, rig, names, curves):
    owned_paths = set()
    owned_constraints = {}
    inventory = None
    generated = bool(api is not None and getattr(api, 'setup', None) is not None
                     and api.setup.has_generated(rig))
    # The upper source can be driven by an IK constraint on its lower child
    # while having no own constraint. Always validate the complete generated
    # chain, including mode drivers, even for an Upper-only selection.
    if generated or any(pb.constraints for pb in rig.pose.bones if pb.name in names):
        inventory = api.limb._validate_inventory(rig)
        for key, entry in inventory['rigs'].items():
            if not set(entry['chain']).intersection(names):
                continue
            _require(api.match.mode_for_rig(rig, entry) == 'FK',
                     'Pose Offset currently requires FK for the affected limb; use Switch with Match explicitly first.')
            target = rig.pose.bones[entry['target'].name]
            mode_path = api.match.property_path(target)
            _require(not any(driver.data_path == mode_path for driver in rig.animation_data.drivers),
                     'An external driver controls this limb\'s IK/FK mode; its original Action was kept.')
            _require(all(_constant_zero(curve) for curve in curves if curve.data_path == mode_path),
                     'This limb changes IK/FK during the animation; its original Action was kept.')
            for pb, constraint, record in entry['entries']:
                if api.match.is_switch_constraint(rig, pb, constraint, record):
                    _require(api.match.validate_constraint_influence(rig, pb, constraint, record),
                             'This limb needs verified FK switching before an offset.')
                    owned_constraints[(pb.name, constraint.name)] = constraint
                    owned_paths.add(constraint.path_from_id('influence'))
    for name in names:
        pb = rig.pose.bones[name]
        _require(pb.rotation_mode == 'QUATERNION' or pb.rotation_mode in {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'},
                 'Pose Offset supports Euler or quaternion FK rotation, not Axis Angle.')
        for constraint in pb.constraints:
            _require((name, constraint.name) in owned_constraints and
                     (constraint.mute or abs(float(constraint.influence)) <= 1e-8),
                     name + ': an active or unrelated constraint controls this Pose region.')
        prefix = pb.path_from_id()
        for curve in curves:
            _require(not curve.data_path.startswith(prefix + '.constraints['),
                     'Animated constraint settings in this region require a separate offset workflow.')
        for driver in rig.animation_data.drivers:
            _require(not driver.data_path.startswith(prefix) or driver.data_path in owned_paths,
                     name + ': a driver controls this Pose region.')


def _selection(api, rig, values, bone_names):
    if bone_names is not None:
        names = set(bone_names)
        _require(names and all(type(name) is str for name in names) and names <= set(rig.pose.bones.keys()),
                 'Choose native bones present in this Pose before offsetting.')
        names.intersection_update(values)
        _require(names, 'The selected region has no channels in this Pose.')
        return names
    selected = {pb.name for pb in rig.pose.bones
                if (pb if hasattr(pb, 'select') else pb.bone).select}
    direct = selected.intersection(values)
    if direct:
        return direct
    if selected:
        from . import control_pose_capture as capture, forearm_original_inventory
        try:
            inventory = forearm_original_inventory.validate(rig)
            resolved = capture._resolve_names(rig, inventory, 'SELECTED', True)
        except (ValueError, KeyError, TypeError):
            resolved = set()
        matched = set(resolved).intersection(values)
        if matched:
            return matched
    # Like ordinary Pose application, a wholly unrelated selection uses the
    # asset's authored destination region. Explicit callers can narrow it.
    return set(values)


def _mirror_values(api, rig, values, metadata):
    if metadata is None:
        # The legacy mirror helper fills missing channels from the currently
        # evaluated pose. A whole-Action fixed delta must not depend on the
        # frame from which the user invoked it.
        _require(all(api.poses._complete_native_transforms(fields) for fields in values.values()),
                 'Mirrored animation offsets need complete saved transforms; save this partial Pose with Save Pose first.')
    from .control_pose_mirror import mirrored_channels
    return mirrored_channels(rig, values, metadata=metadata)


def prepare(context, rig, asset, *, strength=.5, bone_names=None, flipped=False):
    """Read-only admission and immutable channel sampling plan."""
    strength = _factor(strength)
    _require(type(flipped) is bool, 'flipped must be a boolean.')
    api = _modules()
    api.worklist._idle(context)
    _require(rig is not None and rig.type == 'ARMATURE' and not rig.library and not rig.data.library
             and not rig.override_library and rig.is_editable and rig.data.is_editable,
             'Pose Offset needs the local editable animation rig.')
    _require(context.mode in {'OBJECT', 'POSE'} and api.pose._pose_owner(rig),
             'Finish Edit Mode and use one local rig before offsetting.')
    _require(context.scene.objects.get(rig.name) is rig and context.view_layer.objects.get(rig.name) is rig,
             'Return to this animation\'s scene and view layer before offsetting.')
    _require(not getattr(getattr(context, 'screen', None), 'is_animation_playing', False),
             'Stop playback before applying an animation offset.')
    _require(not api.pose.active(rig), 'Return to Animation and select Custom before applying an animation offset.')
    _require(not api.original.active(rig) and not api.rest.is_rest(rig),
             'Return to Controls and Pose Position before applying an animation offset.')
    _require(not api.composition.active(rig) and not api.ua.active_preview(rig),
             'Close the unrelated animation preview before applying a Pose Offset.')
    _require('character_designer_rigging_session_v1' not in rig,
             'Captured Rigging input sessions need their dedicated edit workflow.')
    saved, item = api.worklist.state(context), api.worklist.current(context)
    _require(item is not None and item.rig is rig and item.side == 'CUSTOM',
             'Select this animation\'s editable Custom version first.')
    original = item.custom_action
    _require(original is not None and not original.library and not original.asset_data,
             'Pose Offset needs a local editable Custom Action, not a Pose asset.')
    _require(not getattr(item, 'new_animation', False) and not getattr(item, 'composition_json', ''),
             'New-action handoffs and Composition need their dedicated edit workflow.')
    _require(not any(key in original for key in ('character_designer_rigging_capture_v1',
             'character_designer_rigging_input_action_v1')),
             'Captured Rigging Actions need their dedicated edit workflow.')
    ad = rig.animation_data
    slot = api.worklist._slot(original, item.custom_slot)
    _require(ad is not None and ad.action is original and ad.action_slot is not None
             and ad.action_slot.handle == slot.handle and not ad.use_tweak_mode,
             'The active Action/slot changed; select Custom again.')
    _require(not (ad.use_nla and any(not track.mute and track.strips for track in ad.nla_tracks))
             and ad.action_blend_type == 'REPLACE' and ad.action_influence == 1.0,
             'An active NLA or weighted Action needs a separate offset workflow.')
    revision_value = None
    if getattr(item, 'current_rig_revision', ''):
        revision_value = api.revision.validate(context, item.item_id)
    else:
        api.worklist._check_baseline(item)
        _require(not any(key in original for key in (api.revision.TOKEN, api.revision.CHECKPOINT)),
                 'This Action lost its current-Rig row ownership; its recovery was kept.')
    values = api.poses._asset_channels(asset, rig)
    _require(values is not None and values, 'Pose Offset needs a native-bone Pose asset, not generated-control channels.')
    metadata = getattr(values, 'metadata', None)
    api.poses._compatible(rig, values, metadata)
    if flipped:
        values = _mirror_values(api, rig, values, metadata)
    names = _selection(api, rig, values, bone_names)
    _require(names, 'This Pose has no matching native destinations.')
    _require(all(set(values[name]) <= set(api.poses._TRANSFORMS) for name in names),
             'Bendy-bone shape offsets are not supported; no animation was changed.')
    bag = _bag(original, slot.handle)
    curves = tuple(bag.fcurves)
    _native_fk(api, rig, names, curves)
    channels = {}
    keyed = []
    for curve in curves:
        parsed = _curve_name(curve)
        if parsed and parsed[0] in names:
            name, prop, index = parsed
            _require(prop in _TRANSFORMS and 0 <= index < _TRANSFORMS[prop], 'Unsupported affected rotation channels.')
            _require(not curve.modifiers and not getattr(curve, 'sampled_points', ()) and not curve.mute,
                     'Affected channels with modifiers, samples or mute need a separate offset workflow.')
            _require(parsed not in channels, 'Duplicate transform channels are ambiguous.')
            channels[parsed] = curve
            keyed.extend(float(point.co[0]) for point in curve.keyframe_points)
    for name in names:
        mode = rig.pose.bones[name].rotation_mode
        expected = 'rotation_quaternion' if mode == 'QUATERNION' else 'rotation_euler'
        _require(all(prop == expected for bone, prop, index in channels if bone == name and prop.startswith('rotation_')),
                 name + ': Action rotation channels do not match its current rotation mode.')
    start, end = map(float, original.frame_range)
    frames = _frames(start, end, keyed)
    _require(len(frames) * len(names) * 10 <= _MAX_POINTS,
             'This Pose Offset would create too many sampled keys in one transaction.')
    defaults = {name: {prop: tuple(getattr(rig.pose.bones[name], prop)) for prop in _TRANSFORMS}
                for name in names}
    for name in names:
        location, _rotation, _scale = _target(values[name], rig.pose.bones[name].rotation_mode)
        _require(not rig.data.bones[name].use_connect or max(abs(value) for value in location) <= 2e-6,
                 name + ': this connected bone cannot represent the Pose location offset.')
    hashes = {s.handle: _action_hash(api, original, s.handle) for s in original.slots if s.target_id_type == 'OBJECT'}
    return dict(api=api, context=context, rig=rig, saved=saved, item=item, original=original,
        slot=slot.handle, names=sorted(names), values={name: values[name] for name in names},
        strength=strength, frames=frames, defaults=defaults, channels=channels,
        mode={name: rig.pose.bones[name].rotation_mode for name in names}, hashes=hashes,
        source_hash=_action_hash(api, item.source_action, item.source_slot),
        revision_value=revision_value, asset_name=getattr(asset, 'name', 'Pose'), flipped=flipped,
        compatibility='asset_rest_metadata' if metadata is not None else 'legacy_rig_baseline_and_slot_only')


def _target(fields, mode):
    from mathutils import Euler, Quaternion, Vector
    loc, scale = [0., 0., 0.], [1., 1., 1.]
    rotations = [prop for prop in fields if prop.startswith('rotation_')]
    _require(len(rotations) <= 1, 'The Pose mixes rotation representations.')
    for prop, default in (('location', loc), ('scale', scale)):
        for index, value in fields.get(prop, {}).items():
            default[index] = float(value)
    rotation = Quaternion((1., 0., 0., 0.))
    if rotations:
        prop = rotations[0]
        values = [1., 0., 0., 0.] if prop == 'rotation_quaternion' else [0.] * (4 if prop == 'rotation_axis_angle' else 3)
        if prop == 'rotation_axis_angle':
            values[2] = 1.
        for index, value in fields[prop].items():
            values[index] = float(value)
        rotation = (Quaternion(values) if prop == 'rotation_quaternion' else
                    Euler(values, mode if mode != 'QUATERNION' else 'XYZ').to_quaternion()
                    if prop == 'rotation_euler' else Quaternion(Vector(values[1:]), values[0]))
    _require(all(math.isfinite(value) for value in (*loc, *scale, *rotation)) and
             rotation.magnitude > 1e-8 and all(value > 0 for value in scale),
             'The Pose contains singular, reflected or non-finite transforms.')
    rotation.normalize()
    if rotation.w < 0:
        rotation.negate()
    return loc, rotation, scale


def _sample(plan):
    from mathutils import Euler, Quaternion
    result = {}
    factor = plan['strength']
    for name in plan['names']:
        mode = plan['mode'][name]
        rotation_prop = 'rotation_quaternion' if mode == 'QUATERNION' else 'rotation_euler'
        location, target, scale = _target(plan['values'][name], mode)
        delta = Quaternion((1., 0., 0., 0.)).slerp(target, factor)
        changed_location = {i for i, value in enumerate(location) if value != 0 and factor != 0}
        changed_scale = {i for i, value in enumerate(scale) if value != 1 and factor != 0}
        changed_rotation = abs(float(delta.angle)) > 1e-10
        previous_quaternion = previous_euler = None
        for frame in plan['frames']:
            def value(prop, index):
                curve = plan['channels'].get((name, prop, index))
                answer = float(curve.evaluate(frame)) if curve is not None else plan['defaults'][name][prop][index]
                _require(math.isfinite(answer), 'The source animation has a non-finite sampled transform.')
                return answer
            if changed_rotation:
                rotation_values = [value(rotation_prop, index) for index in range(_TRANSFORMS[rotation_prop])]
                source = Quaternion(rotation_values) if mode == 'QUATERNION' else Euler(rotation_values, mode).to_quaternion()
                _require(source.magnitude > 1e-8, 'The source animation has a zero quaternion.')
                source.normalize()
                wanted = delta @ source
                wanted.normalize()
                if previous_quaternion is not None and wanted.dot(previous_quaternion) < 0:
                    wanted.negate()
                previous_quaternion = wanted.copy()
                if mode != 'QUATERNION':
                    wanted = wanted.to_euler(mode, previous_euler) if previous_euler is not None else wanted.to_euler(mode)
                    previous_euler = wanted.copy()
                for index, component in enumerate(wanted):
                    result.setdefault((name, rotation_prop, index), []).append((frame, float(component)))
            for index in changed_location:
                result.setdefault((name, 'location', index), []).append((frame, value('location', index) + factor * location[index]))
            for index in changed_scale:
                result.setdefault((name, 'scale', index), []).append((frame, value('scale', index) * scale[index] ** factor))
    _require(all(math.isfinite(value) and abs(value) <= 3.402823466e38
                 for points in result.values() for _frame, value in points),
             'The offset cannot be represented by finite Blender key values.')
    return result


def _write(copy_action, plan, sampled):
    bag = _bag(copy_action, plan['slot'])
    for curve in tuple(bag.fcurves):
        parsed = _curve_name(curve)
        if parsed in sampled:
            bag.fcurves.remove(curve)
    for (name, prop, index), points in sampled.items():
        path = 'pose.bones[' + json.dumps(name, ensure_ascii=False) + '].' + prop
        curve = bag.fcurves.new(path, index=index)
        curve.keyframe_points.add(len(points))
        for point, (frame, value) in zip(curve.keyframe_points, points):
            point.co = frame, value
            point.interpolation = 'LINEAR'
        curve.extrapolation = 'CONSTANT'
        curve.update()


def _clone_containers(value):
    if isinstance(value, dict):
        return {key: _clone_containers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone_containers(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone_containers(item) for item in value)
    return value


def _unaffected(api, action, slot, affected):
    """Complete native curve settings/keys/handles for every untouched curve."""
    payload = []
    for layer_index, layer in enumerate(action.layers):
        for strip_index, strip in enumerate(layer.strips):
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    if bag.slot_handle != slot or _curve_name(curve) not in affected:
                        payload.append((layer_index, strip_index, bag.slot_handle,
                                        api.fingerprint._curve_payload(curve)))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode('utf8')).hexdigest()


def _publish(plan, action):
    api, rig, saved, item = (plan[key] for key in ('api', 'rig', 'saved', 'item'))
    row = api.revision._row(item)
    new_id, token = uuid.uuid4().hex, uuid.uuid4().hex
    label = plan.get('variant_label', plan['asset_name'] + ' Offset')
    row.update(item_id=new_id, name=item.name + ' · ' + label,
               custom_action=action, custom_slot=plan['slot'], side='CUSTOM')
    if 'sync_selected' in row:
        row['sync_selected'] = False
    checkpoint = None
    if plan['revision_value'] is not None:
        value = _clone_containers(plan['revision_value'])
        row.update(current_rig_revision=token, work_item_id=uuid.uuid4().hex,
                   clip_key='local-current:' + new_id,
                   link_identity=api.revision._json({'schema': api.revision.SCHEMA, 'token': token,
                       'local_only': True, 'historical_item_id': item.item_id}))
        value['rows'].append(row)
        value['source_curve_hashes'][new_id] = value['source_curve_hashes'][item.item_id]
        value['offset_parent_item_id'] = item.item_id
        value['offset_parent_checkpoint'] = item.custom_action[api.revision.CHECKPOINT]
        references = {}
        history = dict(rig[api.revision._modules().target.REFS])
        checkpoint = api.bpy.data.actions.new('Pose Offset Work Checkpoint')
        plan['owned_checkpoint'] = checkpoint
        encoded = api.revision._encode(value, references, history, rig, custom=action, token=token)
        api.revision._store(checkpoint, encoded, references)
        action[api.revision.TOKEN], action[api.revision.CHECKPOINT] = token, checkpoint
        probe = SimpleNamespace(**row)
        probe.bl_rna = item.bl_rna
        api.revision._read(plan['context'], probe, api.revision._modules())
    new_item = None
    try:
        index = len(saved.items)
        new_item = saved.items.add()
        new_item.item_id = new_id
        plan['owned_row_id'] = new_id
        for key, value in row.items():
            setattr(new_item, key, value)
        if checkpoint is not None:
            api.revision.validate(plan['context'], new_id)
        return new_item, checkpoint
    except Exception:
        if new_item is not None:
            saved.items.remove(index)
        raise


def prepare_variant(context, rig, *, label='Pose Blend'):
    """Capture one genuine Custom owner before an Auto Key preview.

    No FK restriction: the pose service owns IK matching. Whole-Action/source
    hashes are captured here and checked at publication, never per mouse tick.
    """
    api = _modules()
    _require(type(label) is str and label.strip() and len(label) <= 120, 'Choose a short Pose variant label.')
    api.worklist._idle(context)
    _require(rig is not None and rig.type == 'ARMATURE' and not rig.library and not rig.data.library
             and not rig.override_library and rig.is_editable and rig.data.is_editable
             and context.mode in {'OBJECT', 'POSE'} and api.pose._pose_owner(rig),
             'Auto Key needs this local editable animation rig.')
    _require(context.scene.objects.get(rig.name) is rig and context.view_layer.objects.get(rig.name) is rig,
             'Return to this animation\'s scene and view layer before keying.')
    _require(not getattr(getattr(context, 'screen', None), 'is_animation_playing', False),
             'Stop playback before keying a Pose variant.')
    _require(not api.pose.active(rig), 'Base Pose drafts use their own keying workflow.')
    _require(not api.original.active(rig) and not api.rest.is_rest(rig),
             'Return to Controls and Pose Position before keying a Custom variant.')
    _require(not api.composition.active(rig) and not api.ua.active_preview(rig),
             'Close the unrelated animation preview before keying a Custom variant.')
    saved, item = api.worklist.state(context), api.worklist.current(context)
    _require(item is not None and item.rig is rig and item.side == 'CUSTOM',
             'Source is read-only. Select the editable Custom version before enabling Auto Key.')
    original = item.custom_action
    _require(original is not None and not original.library and not original.asset_data,
             'Auto Key needs a local editable Custom Action.')
    _require(not getattr(item, 'new_animation', False) and not getattr(item, 'composition_json', '')
             and api.worklist._rigging_module(rig, original) is None,
             'This input needs its dedicated animation keying workflow.')
    _require(not any(key in original for key in (api.pose.SESSION_KEY, api.pose.REFS_KEY)),
             'A retained Base Pose draft cannot be silently treated as a worklist Custom.')
    ad, slot = rig.animation_data, api.worklist._slot(original, item.custom_slot)
    _require(ad is not None and ad.action is original and ad.action_slot is not None
             and ad.action_slot.handle == slot.handle and not ad.use_tweak_mode,
             'The active Custom Action or slot changed.')
    _require(not (ad.use_nla and any(not track.mute and track.strips for track in ad.nla_tracks))
             and ad.action_blend_type == 'REPLACE' and ad.action_influence == 1.,
             'Active NLA or weighted Actions need their dedicated keying workflow.')
    value = None
    if getattr(item, 'current_rig_revision', ''):
        value = api.revision.validate(context, item.item_id)
    else:
        api.worklist._check_baseline(item)
        _require(not any(key in original for key in (api.revision.TOKEN, api.revision.CHECKPOINT)),
                 'This Action lost its current-Rig row ownership; recovery was kept.')
    capture = api.target._capture(context, rig)
    api.target._validate_capture(context, capture)
    return dict(variant_api=1, phase='prepared', api=api, context=context, rig=rig,
        saved=saved, item=item, item_id=item.item_id, original=original, slot=slot.handle,
        row_identity={key: value for key, value in api.revision._row(item).items()
                      if key in api.revision._IDENTITY_FIELDS},
        source=item.source_action, source_slot=item.source_slot,
        source_hash=_action_hash(api, item.source_action, item.source_slot),
        hashes={s.handle: _action_hash(api, original, s.handle) for s in original.slots if s.target_id_type == 'OBJECT'},
        capture=capture, previous=(saved.active_index, saved.status, saved.has_error),
        previous_target=context.window_manager.character_designer_animation.target,
        frame=context.scene.frame_current + context.scene.frame_subframe,
        asset_name=label.strip(), variant_label=label.strip(), revision_value=value)


def _variant_owner(plan, *, final=False):
    _require(isinstance(plan, dict) and plan.get('variant_api') == 1
             and plan.get('phase') in {'prepared', 'staged'}, 'This Pose variant session is no longer active.')
    api, context, rig, saved = (plan[key] for key in ('api', 'context', 'rig', 'saved'))
    item = api.worklist._item(saved, plan['item_id'])
    _require(item.rig is rig and item.side == 'CUSTOM' and item.custom_action is plan['original']
             and item.custom_slot == plan['slot'] and item.source_action is plan['source']
             and item.source_slot == plan['source_slot'], 'The Custom row or Source identity changed during Pose Blend.')
    current = api.worklist.current(context)
    _require(current is not None and current.item_id == plan['item_id'], 'Another worklist row became active.')
    identity = {key: value for key, value in api.revision._row(item).items()
                if key in api.revision._IDENTITY_FIELDS}
    _require(identity == plan['row_identity'], 'The worklist identity changed during Pose Blend.')
    ad = rig.animation_data
    staged = plan.get('staged_action')
    _require(ad is not None and (ad.action is plan['original'] or staged is not None and ad.action is staged)
             and ad.action_slot is not None and ad.action_slot.handle == plan['slot'],
             'Another Action or slot became active during Pose Blend.')
    _require(context.scene.frame_current + context.scene.frame_subframe == plan['frame'],
             'The frame changed during Pose Blend; no variant was published.')
    if final:
        api.target._validate_capture(context, plan['capture'])
        _require(all(_action_hash(api, plan['original'], handle) == digest for handle, digest in plan['hashes'].items())
                 and _action_hash(api, plan['source'], plan['source_slot']) == plan['source_hash'],
                 'The original Custom or Source content changed during Pose Blend.')
        if plan['revision_value'] is not None:
            api.revision.validate(context, plan['item_id'])
        else:
            api.worklist._check_baseline(item)
    return item


def new_variant_action(plan):
    """Create the sole owned full Action copy at confirmation, before keying."""
    _variant_owner(plan)
    _require(plan['phase'] == 'prepared' and plan.get('staged_action') is None,
             'This Pose Blend already created its editable copy.')
    action = plan['original'].copy()
    plan['staged_action'] = action
    plan['phase'] = 'staged'
    action.name = plan['original'].name + ' · ' + plan['variant_label']
    action.use_fake_user = True
    _require(all(_action_hash(plan['api'], action, handle) == digest for handle, digest in plan['hashes'].items()),
             'The staged Action did not retain every original slot.')
    return action


def publish_variant(plan, action, *, activate=True, preserve_context=True):
    """Publish a confirmed keyed copy at the current frame, without activation jumps."""
    _require(type(activate) is bool and type(preserve_context) is bool and preserve_context,
             'Keyed Pose publication requires preserving its current context.')
    _variant_owner(plan, final=True)
    _require(plan['phase'] == 'staged' and action is plan.get('staged_action'),
             'Only this Pose Blend\'s owned staged Action can be published.')
    api, rig, context = (plan[key] for key in ('api', 'rig', 'context'))
    _require(activate or rig.animation_data.action is plan['original'],
             'Restore the original binding before retaining an inactive keyed variant.')
    _require(not action.library and not action.asset_data
             and action.users - int(action.use_fake_user) - int(rig.animation_data.action is action) == 0,
             'The staged Pose Action gained an unrelated user; it was retained.')
    # The generic pose keying service uses an unfaked scratch while inserting
    # keys. A published worklist Custom must remain retained after Remove.
    action.use_fake_user = True
    action[KEYED_RECORD] = json.dumps(dict(version=1, parent_action=plan['original'].name_full,
        parent_slot=plan['slot'], label=plan['variant_label'], frame=plan['frame']), ensure_ascii=False)
    item, _checkpoint = _publish(plan, action)
    if activate:
        ad = rig.animation_data
        ad.action = action
        ad.action_slot = api.worklist._slot(action, plan['slot'])
        plan['saved'].active_index = next(i for i, row in enumerate(plan['saved'].items) if row.item_id == item.item_id)
        if plan['revision_value'] is None:
            api.worklist._association(item)
    _require(context.scene.frame_current + context.scene.frame_subframe == plan['frame'],
             'Pose publication changed the frame unexpectedly.')
    plan['phase'] = 'published'
    plan['saved'].status, plan['saved'].has_error = 'Pose Blend keyed in a new editable Custom version.', False
    return dict(action=action, item_id=item.item_id, frame=plan['frame'])


def rollback_variant(plan):
    """Undo only this session's publication; a prepared-only cancellation is inert."""
    _require(isinstance(plan, dict) and plan.get('variant_api') == 1, 'Invalid Pose variant recovery plan.')
    if plan.get('phase') == 'rolled_back':
        return
    if plan.get('staged_action') is None:
        plan['phase'] = 'rolled_back'
        return
    api, rig, context = (plan[key] for key in ('api', 'rig', 'context'))
    action = plan['staged_action']
    # Native recovery validates the actual fresh Rest/metadata first. If it
    # refuses, keep every owned ID as a recovery object rather than unlinking it.
    api.revision._restore_current(context, rig, plan['capture'], api.revision._modules())
    context.window_manager.character_designer_animation.target = plan['previous_target']
    row_id = plan.get('owned_row_id')
    if row_id:
        index = next((i for i, row in enumerate(plan['saved'].items) if row.item_id == row_id), None)
        if index is not None:
            plan['saved'].items.remove(index)
    plan['saved'].active_index, plan['saved'].status, plan['saved'].has_error = plan['previous']
    action.pop(api.revision.CHECKPOINT, None)
    checkpoint = plan.get('owned_checkpoint')
    if checkpoint is not None:
        checkpoint.pop(api.revision.REFS, None)
        checkpoint.use_fake_user = False
        if checkpoint.users == 0:
            api.bpy.data.actions.remove(checkpoint)
    action.use_fake_user = False
    if action.users == 0:
        api.bpy.data.actions.remove(action)
    plan['phase'] = 'rolled_back'


def create(context, rig, asset, *, strength=.5, bone_names=None, flipped=False, activate=True):
    """Create a retained worklist variant; never replace the original row/Action."""
    _require(type(activate) is bool, 'activate must be a boolean.')
    plan = prepare(context, rig, asset, strength=strength, bone_names=bone_names, flipped=flipped)
    sampled = _sample(plan) if plan['strength'] else None
    api, saved, original = (plan[key] for key in ('api', 'saved', 'original'))
    previous = (saved.active_index, saved.status, saved.has_error)
    capture = api.target._capture(context, rig)
    api.target._validate_capture(context, capture)
    previous_target = context.window_manager.character_designer_animation.target
    unaffected = _unaffected(api, original, plan['slot'], set(sampled or {}))
    action = checkpoint = new_item = None
    activation_attempted = False
    try:
        action = original.copy()
        action.name = original.name + ' · ' + plan['asset_name'] + ' Offset'
        action.use_fake_user = True
        for handle, digest in plan['hashes'].items():
            _require(_action_hash(api, action, handle) == digest, 'The Action copy did not preserve all slot content.')
        if sampled is not None:
            _write(action, plan, sampled)
        _require(_unaffected(api, action, plan['slot'], set(sampled or {})) == unaffected,
                 'An untouched curve or its handles changed; the original Action was kept.')
        action[RECORD] = json.dumps(dict(version=1, original_action=original.name_full,
            original_slot=plan['slot'], original_fingerprints=plan['hashes'],
            asset=plan['asset_name'], compatibility=plan['compatibility'], names=plan['names'], flipped=plan['flipped'],
            strength=plan['strength'], frame_start=plan['frames'][0], frame_end=plan['frames'][-1],
            sampling='integer frames plus authored transform key times; linear affected channels',
            subframe_equivalence=False), ensure_ascii=False, allow_nan=False)
        new_item, checkpoint = _publish(plan, action)
        if activate:
            activation_attempted = True
            api.worklist.activate(context, new_item.item_id, 'CUSTOM')
        _require(all(_action_hash(api, original, handle) == digest for handle, digest in plan['hashes'].items()),
                 'The original Custom Action changed during offset preparation.')
        _require(_action_hash(api, plan['item'].source_action, plan['item'].source_slot) == plan['source_hash'],
                 'The read-only Source changed during offset preparation.')
        saved.status, saved.has_error = 'Pose Offset created as a new editable Custom version.', False
        return dict(action=action, item_id=new_item.item_id, strength=plan['strength'],
                    bones=plan['names'], sample_count=len(plan['frames']), compatibility=plan['compatibility'],
                    subframe_equivalence=False)
    except Exception as error:
        if activation_attempted:
            try:
                api.revision._restore_current(context, rig, capture, api.revision._modules())
                context.window_manager.character_designer_animation.target = previous_target
            except Exception as restore_error:
                # Keep every owned recovery object if the actual live restore
                # cannot be proved; never delete the currently bound Action.
                raise RuntimeError('Pose Offset failed; live binding recovery needs review: ' + str(restore_error)) from error
        if plan.get('owned_row_id'):
            index = next((i for i, candidate in enumerate(saved.items) if candidate.item_id == plan['owned_row_id']), None)
            if index is not None:
                saved.items.remove(index)
        saved.active_index, saved.status, saved.has_error = previous
        if action is not None:
            action.pop(api.revision.CHECKPOINT, None)
        checkpoint = plan.get('owned_checkpoint', checkpoint)
        if checkpoint is not None:
            checkpoint.pop(api.revision.REFS, None)
            checkpoint.use_fake_user = False
            if checkpoint.users == 0:
                api.bpy.data.actions.remove(checkpoint)
        if action is not None:
            action.use_fake_user = False
            if action.users == 0:
                api.bpy.data.actions.remove(action)
        raise
