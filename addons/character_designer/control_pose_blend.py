"""Finite, owner-checked Pose Asset blending through the existing control graph.

Sessions live only in the invoking operator. There is no registration, timer,
frame handler, persistent cache, or change to a rig's Rest/asset baseline here.
Every preview starts from the same captured channels; it never accumulates the
previous preview. Existing IK/FK matching remains the authority for constraints.
"""
from copy import deepcopy
import math
import uuid

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector


class NativeBlendRequired(ValueError):
    """The asset already targets control channels and should use native Blend."""


class BlendStateChanged(ValueError):
    """The session no longer owns the current state; foreign edits are kept."""


def _poses():
    from . import control_pose_assets
    return control_pose_assets


def _selection_bone(pb):
    return pb if hasattr(pb, 'select') else pb.bone


def _pointer(value):
    return value.as_pointer() if value is not None else None


def _simple(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, 'as_pointer'):
        return ('RNA', _pointer(value))
    if hasattr(value, 'items'):
        return tuple(sorted((key, _simple(item)) for key, item in value.items()))
    return tuple(_simple(item) for item in value)


def _constraint_signature(con):
    # Writable fields include targets, spaces and limits; omit derived getters.
    fields = []
    for prop in con.bl_rna.properties:
        if prop.identifier == 'rna_type' or prop.is_readonly or prop.type == 'COLLECTION':
            continue
        fields.append((prop.identifier, _simple(getattr(con, prop.identifier))))
    return _pointer(con), con.type, tuple(fields)


def _drivers(owner):
    ad = owner.animation_data
    if ad is None:
        return ()
    return tuple((curve.data_path, curve.array_index, curve.mute,
                  curve.driver.type, curve.driver.expression,
                  tuple((variable.name, variable.type,
                         tuple((_pointer(target.id), target.data_path,
                                target.bone_target, target.transform_type,
                                target.transform_space) for target in variable.targets))
                        for variable in curve.driver.variables))
                 for curve in ad.drivers)


def _binding(rig):
    ad = rig.animation_data
    if ad is None:
        return None
    return (_pointer(ad.action), ad.action_slot.handle if ad.action_slot else None,
            ad.use_nla, ad.action_blend_type, ad.action_influence, ad.action_extrapolation,
            tuple((_pointer(track), track.name, track.mute, track.is_solo,
                   tuple((_pointer(strip), _pointer(strip.action), strip.frame_start,
                          strip.frame_end, strip.blend_type, strip.influence, strip.mute)
                         for strip in track.strips)) for track in ad.nla_tracks))


def _worklist_binding(context, rig):
    saved = getattr(context.scene, 'character_designer_animation_worklist', None)
    if saved is None:
        return None
    return (_pointer(saved.rig), saved.active_index,
            tuple((row.item_id, _pointer(row.rig), _pointer(row.source_action), row.source_slot,
                   _pointer(row.custom_action), row.custom_slot, row.side,
                   row.current_rig_revision, row.link_identity) for row in saved.items))


def _pose_test_binding(rig):
    from . import animation_pose_test as pose
    action = rig.animation_data.action if rig.animation_data else None
    return (rig.get(pose.TOKEN_KEY), action.get(pose.SESSION_KEY) if action else None,
            _simple(action.get(pose.REFS_KEY, {})) if action else None)


def _state(context, rig):
    poses = _poses()
    from . import body_original_mode as original, bone_collections, bone_display
    return {
        'rig': (_pointer(rig), _pointer(rig.data), rig.name, rig.data.name),
        'context': (_pointer(context.scene), _pointer(context.view_layer),
                    _pointer(getattr(context, 'window', None)), context.mode,
                    _pointer(context.view_layer.objects.active),
                    tuple(sorted(_pointer(obj) for obj in context.selected_objects)),
                    context.scene.frame_current, context.scene.frame_subframe,
                    context.scene.tool_settings.use_keyframe_insert_auto),
        'object': tuple(value for row in rig.matrix_world for value in row),
        'rest': poses.native_rest(rig), 'pose_position': rig.data.pose_position,
        'binding': _binding(rig), 'worklist': _worklist_binding(context, rig),
        'pose_test': _pose_test_binding(rig), 'drivers': (_drivers(rig), _drivers(rig.data)),
        'pose': {pb.name: (pb.rotation_mode,
                          tuple((name, tuple(getattr(pb, name))) for name in poses._TRANSFORMS),
                          tuple((name, _simple(getattr(pb, name))) for name in poses._BBONE),
                          tuple((key, _simple(pb[key])) for key in sorted(pb.keys())),
                          _selection_bone(pb).select)
                 for pb in rig.pose.bones},
        'constraints': tuple((pb.name, tuple(_constraint_signature(con) for con in pb.constraints))
                             for pb in rig.pose.bones),
        'collection_ids': tuple((c.name, _pointer(c), _pointer(c.parent)) for c in rig.data.collections_all),
        'active_bone': rig.data.bones.active.name if rig.data.bones.active else None,
        'original': rig.get(original.SESSION),
        'pole_metadata': poses.match._leg_pole_metadata(rig),
        'layout': bone_collections.snapshot_layout(rig), 'display': bone_display._snapshot(rig),
    }


def _snapshot(context, rig):
    from . import body_original_mode as original
    poses = _poses()
    ad = rig.animation_data
    return {
        'state': _state(context, rig),
        'constraints': [(con, con.mute, float(con.pole_angle) if con.type == 'IK' else None)
                        for pb in rig.pose.bones for con in pb.constraints],
        'action': ad.action if ad else None, 'slot': ad.action_slot if ad else None,
        'had_animation': ad is not None,
        'before_key': {pb.name: (pb.rotation_mode, pb.matrix_basis.copy(), pb.get('ik_fk'))
                       for pb in rig.pose.bones},
        'session_key': original.SESSION,
        'properties': {pb.name: {key: deepcopy(pb[key]) for key in ('ik_fk', 'uniform_scale') if key in pb}
                       for pb in rig.pose.bones},
    }


def _restore(context, session):
    """Restore owned preview leaves only, after the caller's ownership check."""
    from . import bone_collections, bone_display
    poses, rig, saved = _poses(), session['rig'], session['initial']
    state = saved['state']
    for con, mute, angle in saved['constraints']:
        if con.mute != mute:
            con.mute = mute
        if angle is not None and con.pole_angle != angle:
            con.pole_angle = angle
    if poses.match._leg_pole_metadata(rig) != state['pole_metadata']:
        poses.match._restore_leg_pole_metadata(rig, state['pole_metadata'])
    key = saved['session_key']
    if state['original'] is None:
        if key in rig:
            del rig[key]
    elif rig.get(key) != state['original']:
        rig[key] = state['original']
    for name, entry in state['pose'].items():
        pb = rig.pose.bones[name]
        if pb.rotation_mode != entry[0]:
            pb.rotation_mode = entry[0]
        for field, values in entry[1]:
            if tuple(getattr(pb, field)) != values:
                setattr(pb, field, values)
        for field, values in entry[2]:
            if _simple(getattr(pb, field)) != values:
                setattr(pb, field, values)
        for field in ('ik_fk', 'uniform_scale'):
            if field in saved['properties'][name]:
                if pb.get(field) != saved['properties'][name][field]:
                    pb[field] = saved['properties'][name][field]
            elif field in pb:
                del pb[field]
        if _selection_bone(pb).select != entry[4]:
            _selection_bone(pb).select = entry[4]
    if rig.data.bones.active != rig.data.bones.get(state['active_bone'] or ''):
        rig.data.bones.active = rig.data.bones.get(state['active_bone'] or '')
    if tuple((c.name, _pointer(c), _pointer(c.parent)) for c in rig.data.collections_all) != state['collection_ids']:
        raise BlendStateChanged('The bone collections changed during Pose matching; no layout was rebuilt.')
    if bone_collections.snapshot_layout(rig) != state['layout']:
        _restore_layout(rig, state['layout'], state['collection_ids'])
    if bone_display._snapshot(rig) != state['display']:
        bone_display._restore(rig, state['display'])
    bone_collections._FRAME_CACHE.pop(rig.as_pointer(), None)
    poses.match._update(context, rig)


def _restore_layout(rig, layout, identities):
    """Restore visibility/membership in place; never recreate BoneCollections."""
    from . import bone_collections as groups
    data = rig.data
    if tuple((c.name, _pointer(c), _pointer(c.parent)) for c in data.collections_all) != identities:
        raise BlendStateChanged('The bone collections changed during Pose matching; no layout was rebuilt.')
    for saved in layout['collections']:
        collection = data.collections_all[saved['name']]
        if collection.is_visible != saved['visible']:
            collection.is_visible = saved['visible']
        if collection.is_solo != saved['solo']:
            collection.is_solo = saved['solo']
        if collection.is_expanded != saved['expanded']:
            collection.is_expanded = saved['expanded']
        for bone in tuple(collection.bones):
            if bone.name not in saved['bones']:
                collection.unassign(bone)
        for name in saved['bones']:
            if name not in collection.bones:
                collection.assign(data.bones[name])
        for key in tuple(collection.keys()):
            if key not in saved['properties']:
                del collection[key]
        for key, value in saved['properties'].items():
            if _simple(collection.get(key)) != _simple(value):
                collection[key] = value
    if data.collections.active != data.collections_all.get(layout['active'] or ''):
        data.collections.active = data.collections_all.get(layout['active'] or '')
    for name, flags in layout['hidden'].items():
        if data.bones[name].hide != flags[0]:
            data.bones[name].hide = flags[0]
        if data.bones[name].hide_select != flags[1]:
            data.bones[name].hide_select = flags[1]
    for name, hidden in layout['pose_hidden'].items():
        if rig.pose.bones[name].hide != hidden:
            rig.pose.bones[name].hide = hidden
    for key, value in ((groups.PROFILE_KEY, layout['profile']), (groups.AUTO_KEY, layout['automatic']),
                       (groups.NATIVE_ONLY_KEY, layout['native_only']), (groups.BACKUP_KEY, layout['backup']),
                       (groups.BACKUP_REFS_KEY, layout['backup_refs'])):
        if value is None or value == {}:
            data.pop(key, None)
        elif _simple(data.get(key)) != _simple(value):
            data[key] = value


def _check(context, session):
    if session.get('closed'):
        raise BlendStateChanged('This Pose blend has already ended; start it again.')
    rig = session['rig']
    try:
        same = _state(context, rig) == session['expected']
    except (ReferenceError, RuntimeError, KeyError, TypeError):
        same = False
    if not same:
        raise BlendStateChanged('The frame, rig, pose, selection or animation changed outside this Pose blend. '
                                'Current edits were kept; start Blend Pose again.')
    return rig


def _owner(context, rig):
    from . import animation_pose_test
    if animation_pose_test.natural_active(rig):
        raise ValueError('Choose Pose to finish the static Rest test before blending a Pose.')
    if (rig is None or rig.type != 'ARMATURE' or rig.library or rig.data.library
            or rig.override_library or rig.data.override_library
            or context.object != rig or context.mode not in {'OBJECT', 'POSE'}
            or rig.mode == 'EDIT' or not rig.is_editable or not rig.data.is_editable):
        raise ValueError('Blend this Pose on the active local character in Object or Pose Mode.')
    pose_only = bool(animation_pose_test._pose_owner(rig))
    if rig.data.users != 1 and not pose_only:
        raise ValueError('The character Armature data is shared; Pose blend cannot own these changes.')
    if getattr(getattr(context, 'screen', None), 'is_animation_playing', False):
        raise ValueError('Stop playback before blending a Pose.')
    if rig.data.pose_position != 'POSE':
        raise ValueError('Use Pose display before blending a Pose; the Rest skeleton was kept.')
    if rig.animation_data and rig.animation_data.use_tweak_mode:
        raise ValueError('Exit NLA Tweak Mode before blending a Pose.')
    from . import animation_composition, unity_animation, forearm_twist
    if animation_composition.active(rig) or unity_animation.active_preview(rig) is not None:
        raise ValueError('Close the active animation preview before blending a Pose; its draft was kept.')
    if 'character_designer_rigging_session_v1' in rig:
        raise ValueError('Close the live Rigging input preview before blending a Pose.')
    calibration = getattr(forearm_twist, '_SESSION', None)
    if calibration and calibration.get('armature') == rig:
        raise ValueError('Confirm or cancel Forearm calibration before blending a Pose.')
    return pose_only


def _key_domain(context, rig):
    """Admit the actual Action owner before any Auto Key preview can mutate it."""
    if not context.scene.tool_settings.use_keyframe_insert_auto:
        return {'kind': 'NONE'}
    from . import animation_pose_test as pose
    if pose.active(rig):
        action, record, refs = pose._record(context, rig)
        return {'kind': 'POSE_TEST', 'action': action, 'record': deepcopy(record),
                'session_raw': action[pose.SESSION_KEY], 'refs': dict(refs),
                'token': rig[pose.TOKEN_KEY], 'fake': action.use_fake_user}
    ad = rig.animation_data
    action = ad.action if ad else None
    saved = getattr(context.scene, 'character_designer_animation_worklist', None)
    if action and (action.library or saved and any(row.rig == rig and row.source_action == action for row in saved.items)):
        raise ValueError('Source is read-only. Select its editable Custom version before using Auto Key with Blend Pose.')
    customs = ([row for row in saved.items if row.rig == rig and row.custom_action == action]
               if saved is not None and action is not None else [])
    if customs:
        if len(customs) != 1:
            raise ValueError('This Action belongs to several Custom rows; select one unambiguous editing version first.')
        from . import control_pose_offset
        return {'kind': 'WORKLIST', 'plan': control_pose_offset.prepare_variant(context, rig, label='Pose Blend')}
    return {'kind': 'ACTION'}


def _selected_values(rig, values, *, flipped, original_names, respect_selection):
    if not respect_selection:
        return values
    selected = {pb.name for pb in rig.pose.bones if _selection_bone(pb).select}
    sources = {name for name in original_names if bpy.utils.flip_name(name) != name}
    targets = {bpy.utils.flip_name(name) for name in sources}
    if flipped and selected.intersection(sources) and not selected.intersection(targets):
        selected = (selected - sources) | {bpy.utils.flip_name(name) for name in selected.intersection(sources)}
    wanted = selected.intersection(values)
    if not wanted:
        # A selected generated hand target/Pole semantically selects its limb.
        # Unrelated selections retain the existing native-pose fallback to all.
        try:
            inventory = _poses().limb_ik._validate_inventory(rig)
        except (ValueError, RuntimeError, KeyError, TypeError):
            inventory = None
        if inventory:
            for entry in inventory['rigs'].values():
                controls = {entry[key].name for key in ('target', 'pole', 'solver_target') if entry.get(key)}
                if selected.intersection(controls):
                    wanted.update(set(entry['chain']).intersection(values))
    return {name: fields for name, fields in values.items() if name in wanted} if wanted else values


def _basis_fields(rig, name, start, fields):
    poses = _poses()
    loc, rot, scale = start.decompose()
    mode = rig.pose.bones[name].rotation_mode
    euler_mode = mode if mode not in {'QUATERNION', 'AXIS_ANGLE'} else 'XYZ'
    axis, angle = rot.to_axis_angle()
    target = {'location': list(loc), 'scale': list(scale), 'rotation_quaternion': list(rot),
              'rotation_euler': list(rot.to_euler(euler_mode)), 'rotation_axis_angle': [angle, *axis]}
    rotations = [prop for prop in fields if prop.startswith('rotation_')]
    if len(rotations) > 1:
        raise ValueError(f'{name}: this Pose mixes rotation representations.')
    for prop, entries in fields.items():
        if prop not in poses._FIELDS or any(type(index) is not int or not 0 <= index < poses._FIELDS[prop]
                                           or not math.isfinite(value) for index, value in entries.items()):
            raise ValueError(f'{name}: this Pose contains invalid channels.')
        if prop in poses._TRANSFORMS:
            for index, value in entries.items():
                target[prop][index] = value
    if rotations:
        prop, values = rotations[0], target[rotations[0]]
        rot = (Quaternion(values) if prop == 'rotation_quaternion' else
               Euler(values, euler_mode).to_quaternion() if prop == 'rotation_euler' else
               Quaternion(Vector(values[1:]), values[0]))
        if rot.magnitude < 1e-8:
            raise ValueError(f'{name}: this Pose has a zero rotation quaternion.')
    result = Matrix.LocRotScale(Vector(target['location']), rot.normalized(), Vector(target['scale']))
    if (not poses.limb_ik._matrix_is_finite(start) or not poses.limb_ik._matrix_is_finite(result)
            or poses._difference(start, Matrix.LocRotScale(*start.decompose())) > 2e-6):
        raise ValueError(f'{name}: sheared or non-finite local transforms cannot be blended safely.')
    return result


def begin(context, asset=None, *, rig=None, values=None, metadata=None, flipped=False,
          respect_selection=True):
    """Capture one fixed start; ``values`` is also a plain-channel test/API entry.

    A NativeBlendRequired result is deliberate routing, not a failed attempt to
    reinterpret a control-authored asset as an original-bone asset.
    """
    poses = _poses()
    rig = rig or context.object
    pose_only = _owner(context, rig)
    if values is None:
        asset = asset or getattr(context, 'asset', None)
        if asset is None or asset.id_type != 'ACTION':
            raise ValueError('Choose a Pose asset before blending.')
        values = poses._asset_channels(asset, rig)
        if values is None:
            raise NativeBlendRequired('This Pose already targets controls; use native Blend Pose.')
        metadata = getattr(values, 'metadata', None)
    values, metadata = deepcopy(dict(values)), deepcopy(metadata)
    if not values:
        raise ValueError('This Pose has no channels to blend.')
    if set(values) - set(poses.native_rest(rig)):
        raise NativeBlendRequired('This Pose contains control channels; use native Blend Pose.')
    # Classify control-authored assets before applying our Auto Key policy.
    # Native Blend keeps its own owner rules; our Source check follows
    # routing, and still precedes any graph update or preview writes.
    key_domain = _key_domain(context, rig)
    poses.match._update(context, rig)
    original_names = set(values)
    if flipped:
        from .control_pose_mirror import mirrored_channels
        values = (mirrored_channels(rig, values, metadata=metadata) if metadata is not None
                  else mirrored_channels(rig, values))
        metadata = getattr(values, 'metadata', metadata)
    values = _selected_values(rig, values, flipped=flipped, original_names=original_names,
                              respect_selection=respect_selection)
    if metadata is not None:
        metadata = dict(metadata, names=sorted(values))
    native = poses.native_rest(rig)
    if set(values) - set(native):
        raise NativeBlendRequired('This Pose contains control channels; use native Blend Pose.')
    matching = poses._needs_control_matching(rig, values)
    if metadata is not None or matching or poses.BASELINE in rig:
        poses._compatible(rig, values, metadata)
    if metadata is not None:
        if 'stable_ik_bend' in metadata and type(metadata['stable_ik_bend']) is not bool:
            raise ValueError('This Pose has invalid stable IK bend data; no changes were applied.')
        poses._validate_stable_ik_seed_metadata(metadata)
    if rig.animation_data:
        prefixes = tuple(rig.pose.bones[name].path_from_id() for name in values)
        bbone_paths = {rig.pose.bones[name].path_from_id(prop) for name, fields in values.items()
                       for prop in fields if prop in poses._BBONE}
        for curve in rig.animation_data.drivers:
            if (curve.data_path in bbone_paths or any(
                    curve.data_path.startswith(prefix + '.')
                    and any(curve.data_path[len(prefix):].startswith('.' + prop)
                            for prop in ('location', 'rotation_', 'scale'))
                    for prefix in prefixes)):
                raise ValueError('A driver controls this Pose region; no changes were applied.')
    evaluated = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
    order = tuple(sorted(native, key=lambda name: len(rig.pose.bones[name].parent_recursive)))
    start_local, target_local = {}, {}
    for name in order:
        bone = rig.data.bones[name]
        parent = evaluated[bone.parent.name] if bone.parent else Matrix.Identity(4)
        start_local[name] = poses._convert(bone, evaluated[name], parent, invert=True)
        if name in values:
            target_local[name] = _basis_fields(rig, name, start_local[name], values[name])
    initial = _snapshot(context, rig)
    return {'rig': rig, 'initial': initial, 'expected': initial['state'],
            'values': values, 'metadata': metadata, 'start_local': start_local,
            'target_local': target_local, 'evaluated': evaluated, 'order': order,
            'matching': matching, 'pose_only': pose_only, 'factor': 0.,
            'closed': False, 'owned_action': None, 'key_domain': key_domain}


def _desired(session, factor):
    poses, rig = _poses(), session['rig']
    result, bases = {}, {}
    for name in session['order']:
        start = session['start_local'][name]
        basis = start
        if name in session['target_local']:
            end = session['target_local'][name]
            if factor == 1.:
                basis = end.copy()
            else:
                loc, rot, scale = start.decompose()
                loc1, rot1, scale1 = end.decompose()
                basis = Matrix.LocRotScale(loc.lerp(loc1, factor), rot.slerp(rot1, factor),
                                           scale.lerp(scale1, factor))
        bases[name] = basis
        bone = rig.data.bones[name]
        parent = result.get(bone.parent.name, session['evaluated'][bone.parent.name]) if bone.parent else Matrix.Identity(4)
        result[name] = poses._convert(bone, basis, parent)
    return result, bases


def _write_free(context, session, bases):
    """Free native channels need no generated-control graph or new baseline."""
    rig = session['rig']
    for name, fields in session['values'].items():
        pb = rig.pose.bones[name]
        loc, rot, scale = bases[name].decompose()
        if 'location' in fields:
            for index in fields['location']:
                pb.location[index] = loc[index]
        if 'scale' in fields:
            for index in fields['scale']:
                pb.scale[index] = scale[index]
        if any(prop.startswith('rotation_') for prop in fields):
            if pb.rotation_mode == 'QUATERNION':
                pb.rotation_quaternion = rot
            elif pb.rotation_mode == 'AXIS_ANGLE':
                axis, angle = rot.to_axis_angle()
                pb.rotation_axis_angle = (angle, *axis)
            else:
                pb.rotation_euler = rot.to_euler(pb.rotation_mode, pb.rotation_euler.copy())
    _poses().match._update(context, rig)


def preview(context, session, factor):
    """Preview 0..1 from the fixed start, with no key insertion or accumulation."""
    if type(factor) not in {int, float} or not math.isfinite(factor) or not 0. <= factor <= 1.:
        raise ValueError('Pose blend strength must be between 0 and 1.')
    rig = _check(context, session)
    if float(factor) == session['factor']:
        return {'factor': session['factor'], 'bones': len(session['values'])}
    poses = _poses()
    from . import body_original_mode as original, forearm_twist
    with forearm_twist.defer_runtime(context, flush_on_exit=False) as refresh:
        try:
            if session['expected'] != session['initial']['state']:
                _restore(context, session)
            if factor:
                desired, bases = _desired(session, float(factor))
                changed = {name for name, matrix in desired.items()
                           if poses._difference(matrix, session['evaluated'][name]) > 1e-7}
                if session['matching']:
                    if original.active(rig):
                        poses._match_original(context, rig, desired, changed, preserve_modes=True)
                    else:
                        stable, seeds = (), {}
                        metadata = session['metadata']
                        if factor == 1. and metadata and metadata.get('stable_ik_bend', False):
                            inventory = poses.limb_ik._validate_inventory(rig)
                            stable = tuple(key for key, entry in inventory['rigs'].items()
                                if key[0] == 'ARM' and all(name in session['values']
                                    and poses._complete_native_transforms(session['values'][name])
                                    for name in entry['chain']))
                            seeds = poses._validate_stable_ik_seed_metadata(metadata)
                            poses._validate_stable_ik_seed_inventory(rig, inventory, seeds,
                                applied=tuple(key for key in stable
                                    if poses.match.mode_for_rig(rig, inventory['rigs'][key]) == 'IK'))
                        poses._match(context, rig, desired, changed, preserve_modes=True,
                                     sync_controls=True, authored_names=set(session['values']),
                                     stable_ik_bend_limbs=stable, stable_ik_seeds=seeds,
                                     _pose_only_owner=session['pose_only'])
                else:
                    _write_free(context, session, bases)
                for name, fields in session['values'].items():
                    pb = rig.pose.bones[name]
                    start = dict(session['initial']['state']['pose'][name][2])
                    for prop, entries in fields.items():
                        if prop not in poses._BBONE:
                            continue
                        if poses._BBONE[prop] == 1:
                            setattr(pb, prop, start[prop] + (entries[0] - start[prop]) * factor)
                        else:
                            for index, value in entries.items():
                                getattr(pb, prop)[index] = start[prop][index] + (value - start[prop][index]) * factor
                poses.match._update(context, rig)
            refresh()
        except Exception:
            _restore(context, session)
            refresh()
            session['factor'] = 0.
            session['expected'] = _state(context, rig)
            raise
    session['factor'] = float(factor)
    session['expected'] = _state(context, rig)
    return {'factor': session['factor'], 'bones': len(session['values'])}


def _key_preview(context, session):
    """Own the one staged Action explicitly, including failures before binding."""
    poses, rig = _poses(), session['rig']
    if not context.scene.tool_settings.use_keyframe_insert_auto or session['factor'] == 0.:
        return None
    ad = rig.animation_data_create()
    previous, slot = ad.action, ad.action_slot
    domain = session['key_domain']
    if domain['kind'] == 'POSE_TEST':
        from . import animation_pose_test as pose
        action, _record, refs = pose._record(context, rig)
        if (action != domain['action'] or action[pose.SESSION_KEY] != domain['session_raw']
                or rig.get(pose.TOKEN_KEY) != domain['token'] or dict(refs) != domain['refs']):
            raise BlendStateChanged('The Pose test recovery owner changed; its draft and current edits were kept.')
    if domain['kind'] == 'WORKLIST':
        from . import control_pose_offset
        staged = control_pose_offset.new_variant_action(domain['plan'])
    else:
        staged = previous.copy() if previous else bpy.data.actions.new(rig.name + ' Poses')
    session['owned_action'] = staged
    if poses.ASSET_METADATA in staged:
        del staged[poses.ASSET_METADATA]
    if staged.asset_data:
        staged.asset_clear()
    staged.use_fake_user = False
    staged_slot = staged.slots[slot.identifier] if slot else None
    if domain['kind'] == 'POSE_TEST':
        # A fresh token keeps the old carrier/recovery untouched and prevents
        # its inherited token from making the current owner ambiguous.
        token = uuid.uuid4().hex
        payload = dict(domain['record'], token=token, action_id=uuid.uuid4().hex)
        payload['slot'] = staged_slot.identifier
        payload['slot_handle'] = int(staged_slot.handle)
        staged[pose.SESSION_KEY] = pose._pack(payload)
        staged[pose.REFS_KEY] = dict(domain['refs'])
        session['new_pose_token'] = token
    ad.action = staged
    if staged_slot:
        ad.action_slot = staged_slot
    if domain['kind'] == 'POSE_TEST':
        rig[pose.TOKEN_KEY] = session['new_pose_token']
    frame = context.scene.frame_current + context.scene.frame_subframe
    for pb in rig.pose.bones:
        _mode, basis, ik_fk = session['initial']['before_key'][pb.name]
        paths = set()
        if poses._difference(basis, pb.matrix_basis) > 1e-7:
            rotation = ('rotation_quaternion' if pb.rotation_mode == 'QUATERNION' else
                        'rotation_axis_angle' if pb.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
            paths.update(('location', rotation, 'scale'))
        paths.update(prop for prop in session['values'].get(pb.name, {}) if prop in poses._BBONE)
        if ik_fk is not None and ik_fk != pb.get('ik_fk'):
            paths.add('["ik_fk"]')
        for path in paths:
            poses._insert_key(pb, path, frame)
    return staged


def _restore_binding(session):
    rig, saved = session['rig'], session['initial']
    domain = session['key_domain']
    if domain['kind'] == 'WORKLIST':
        from . import control_pose_offset
        control_pose_offset.rollback_variant(domain['plan'])
        session['owned_action'] = None
        return
    if domain['kind'] == 'POSE_TEST':
        from . import animation_pose_test as pose
        token = rig.get(pose.TOKEN_KEY)
        if token not in {domain['token'], session.get('new_pose_token')}:
            raise BlendStateChanged('The Pose test token changed outside this blend; its recovery was retained.')
        rig[pose.TOKEN_KEY] = domain['token']
        domain['action'].use_fake_user = domain['fake']
    ad = rig.animation_data
    if ad is not None:
        ad.action = saved['action']
        if saved['action'] and saved['slot']:
            ad.action_slot = saved['slot']
    staged = session['owned_action']
    if staged is not None:
        if staged.users:
            raise BlendStateChanged('The staged Pose Action gained another owner; it was retained.')
        bpy.data.actions.remove(staged)
        session['owned_action'] = None
    if not saved['had_animation'] and rig.animation_data and not rig.animation_data.drivers and not rig.animation_data.nla_tracks:
        rig.animation_data_clear()


def confirm(context, session):
    """Commit the current preview and optionally key it once in an owned copy."""
    rig = _check(context, session)
    publication = None
    try:
        action = _key_preview(context, session)
        domain = session['key_domain']
        if action is not None and domain['kind'] == 'POSE_TEST':
            from . import animation_pose_test as pose
            current, _record, _refs = pose._record(context, rig)
            if current != action:
                raise BlendStateChanged('The new Pose test carrier could not be verified; recovery was kept.')
            # Preserve the previous carrier, its curves, SESSION and REFS. Its
            # old token is not the rig's current restoration authority.
            if not domain['action'].use_fake_user:
                domain['action'].use_fake_user = True
        elif action is not None and domain['kind'] == 'WORKLIST':
            from . import control_pose_offset
            publication = control_pose_offset.publish_variant(domain['plan'], action,
                                                               activate=True, preserve_context=True)
    except Exception as error:
        try:
            _restore_binding(session)
        except Exception as recovery_error:
            session['closed'] = True
            raise RuntimeError('Pose blend failed; its owned binding recovery needs review: ' + str(recovery_error)) from error
        _restore(context, session)
        session['expected'] = _state(context, rig)
        session['closed'] = True
        raise
    session['closed'] = True
    return {'factor': session['factor'], 'bones': len(session['values']), 'action': action,
            'publication': publication}


def cancel(context, session):
    """Restore the exact start; never overwrite an outside edit or new frame."""
    _check(context, session)
    _restore_binding(session)
    _restore(context, session)
    session['closed'] = True
    return {'cancelled': True}
