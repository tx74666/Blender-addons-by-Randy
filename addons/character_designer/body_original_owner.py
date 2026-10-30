"""Authenticate retained Worklist references during one Original return.

The current native Rest is never repaired or replaced.  A saved Original
session supplies the preceding native geometry, while each retained Action
must independently authenticate that entire preceding armature and its exact
unchanged motion data.  This is not a generic shared-data exception.
"""
import hashlib
import json
import math
from contextlib import contextmanager

import bpy
from mathutils import Matrix, Vector


_FRAME_EPSILON = 1e-6
_FRAME_X_RESIDUAL_LIMIT = 2e-5
_FOOT_REFERENCE_ROLES = frozenset(('FK_TOE_REF', 'AUTO_TOE_REF'))
POSE_REFS = 'character_designer_original_pose_owner_refs_v1'


class _PoseCapability:
    """Invocation-local permission; never a shared-data or Rest recovery proof."""
    pass


def _pose_require(value, message):
    if not value:
        raise ValueError(message)


def _pose_refmap(refs):
    return {'id_' + str(i): value for i, value in enumerate(refs)}


def _same_pose_refs(actual, refs):
    expected = _pose_refmap(refs)
    return (hasattr(actual, 'items') and set(actual) == set(expected)
            and all(actual[key] is value for key, value in expected.items()))


def _pose_history(value, refs, *, add=False, seen=None):
    """Hash complete carrier properties, pinning native IDs without pointers."""
    if isinstance(value, bpy.types.ID):
        index = next((i for i, target in enumerate(refs) if target is value), None)
        if index is None:
            _pose_require(add, 'An Original history carrier changed a native ID reference.')
            index = len(refs)
            refs.append(value)
        if seen is not None:
            seen.add(index)
        return {'id': 'id_' + str(index)}
    if type(value) is str:
        return {'utf8_sha256': hashlib.sha256(value.encode('utf-8')).hexdigest(),
                'length': len(value)}
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        _pose_require(math.isfinite(value), 'An Original history carrier is not finite.')
        return {'float': value.hex()}
    if hasattr(value, 'items'):
        _pose_require(all(type(key) is str for key in value), 'Invalid Original history property key.')
        return {key: _pose_history(value[key], refs, add=add, seen=seen) for key in sorted(value)}
    if not isinstance(value, (str, bytes)) and hasattr(value, '__iter__'):
        return [_pose_history(item, refs, add=add, seen=seen) for item in value]
    raise ValueError('Unsupported Original history carrier value: ' + type(value).__name__)


def _pose_data_paths(value, data, path=()):
    if isinstance(value, bpy.types.ID):
        return [path] if value is data else []
    if hasattr(value, 'items'):
        return [leaf for key, child in value.items()
                for leaf in _pose_data_paths(child, data, path + (key,))]
    if not isinstance(value, (str, bytes)) and hasattr(value, '__iter__'):
        return [leaf for i, child in enumerate(value)
                for leaf in _pose_data_paths(child, data, path + (i,))]
    return []


def _pose_state(context, rig, refs=None):
    from . import animation_worklist_target as target, unity_animation as ua
    data = rig.data
    _pose_require(rig.type == 'ARMATURE' and rig.mode in {'OBJECT', 'POSE'}
                  and not getattr(data, 'is_editmode', False)
                  and context.mode in {'OBJECT', 'POSE'} and not rig.library and not data.library
                  and not rig.override_library and not data.override_library
                  and rig.is_editable and data.is_editable,
                  'Original pose editing needs one local editable armature outside Edit Mode.')
    _pose_require(not context.screen or not context.screen.is_animation_playing,
                  'Pause playback before switching Original or Controls.')
    bound = [obj for obj in bpy.data.objects if obj.type == 'ARMATURE' and obj.data is data]
    _pose_require(len(bound) == 1 and bound[0] is rig,
                  'Another Object shares this Original armature data; its existing rig was kept.')
    _pose_require(context.scene.objects.get(rig.name) is rig
                  and context.view_layer.objects.get(rig.name) is rig
                  and [scene for scene in bpy.data.scenes if scene.objects.get(rig.name) is rig] == [context.scene],
                  'Return to the Original rig scene and view layer.')
    _pose_require(not data.use_fake_user and not getattr(data, 'use_extra_user', False),
                  'An unexplained fake or extra user owns the Original armature data.')
    owners = bpy.data.user_map(subset={data}).get(data, set())
    _pose_require(rig in owners, 'The Original armature lost its native Object owner.')
    actions = []
    ref_count = 1
    for owner in owners:
        paths = _pose_data_paths(dict(owner.items()), data)
        if owner is rig:
            _pose_require(all(len(path) == 2 and path[0] in {target.REFS, POSE_REFS}
                              and type(path[1]) is str for path in paths),
                          'An unknown rig property refers to Original armature data.')
        else:
            _pose_require(isinstance(owner, bpy.types.Action) and not owner.library
                          and not owner.override_library and owner.get(target.ACTION_RIG_KEY) is rig
                          and paths and all(path in {(ua.ORIGINAL_DATA_KEY,), (ua.PREVIEW_DATA_KEY,)} for path in paths),
                          'An unknown owner or property refers to Original armature data.')
            actions.append(owner)
        ref_count += len(paths)
    _pose_require(data.users == ref_count, 'Original armature user counts do not match its exact native references.')
    fresh = refs is None
    refs = [rig, data, context.scene] if fresh else list(refs)
    _pose_require(len(refs) >= 3 and refs[0] is rig and refs[1] is data and refs[2] is context.scene,
                  'The Original pose owner rig, data or scene changed.')
    for action in sorted(actions, key=lambda item: item.name_full):
        _pose_history(action, refs, add=fresh)
    actions.sort(key=lambda item: next(i for i, value in enumerate(refs) if value is item))
    rig_history = {key: rig[key] for key in (target.SESSION, target.REFS, target.RESTORED) if key in rig}
    seen = set(range(3))
    payload = [context.view_layer.name, _pose_history(rig_history, refs, add=fresh, seen=seen),
               [[_pose_history(action, refs, seen=seen), _pose_history(dict(action.items()), refs, add=fresh, seen=seen)]
                for action in actions]]
    _pose_require(seen == set(range(len(refs))), 'The Original pose reference map contains an unexplained native ID.')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                      allow_nan=False).encode('utf-8')).hexdigest()
    rest = json.loads(json.dumps(_saved_rest(ua._rest_state(data), 'Current Original pose-only Rest'), allow_nan=False))
    return rest, digest, refs


def pose_capability(context, rig, *, entering=False):
    from . import body_original_mode as original, limb_ik
    if rig is None or getattr(rig, 'type', None) != 'ARMATURE':
        return None
    raw = rig.get(original.SESSION)
    saved = json.loads(raw) if raw is not None else {}
    _pose_require(type(saved) is dict, 'The Original session is not a valid record.')
    marker = saved.get('pose_only_owner')
    if marker is None:
        if not entering or limb_ik._single_armature_user(rig):
            return None
        _pose_require(POSE_REFS not in rig, 'An unfinished Original pose reference map was kept.')
        rest, digest, refs = _pose_state(context, rig)
    else:
        _pose_require(type(marker) is dict and set(marker) == {'version', 'rest', 'carrier_sha256'}
                      and type(marker['version']) is int and marker['version'] == 1,
                      'The Original pose-only session record is invalid.')
        _saved_rest(marker['rest'], 'Saved Original pose-only Rest')
        native = rig.get(POSE_REFS)
        _pose_require(hasattr(native, 'items') and all('id_' + str(i) in native for i in range(len(native))),
                      'The Original pose-only native reference map is missing or invalid.')
        refs = [native['id_' + str(i)] for i in range(len(native))]
        _pose_require(_same_pose_refs(native, refs), 'The Original pose-only reference map changed.')
        rest, digest, _refs = _pose_state(context, rig, refs)
        _pose_require(rest == marker['rest'] and digest == marker['carrier_sha256'],
                      'Original pose-only Rest or retained history changed; keep the current Original edit.')
    cap = _PoseCapability()
    cap.context, cap.rig, cap.data = context, rig, rig.data
    cap.rest, cap.digest, cap.refs = rest, digest, tuple(refs)
    cap.fields = {'version': 1, 'rest': rest, 'carrier_sha256': digest}
    cap.before_refs = dict(rig[POSE_REFS]) if POSE_REFS in rig else None
    cap.installed, cap.finished, cap.checkpoint = cap.before_refs is not None, False, None
    cap.returning = marker is not None
    return cap


def pose_session_fields(cap):
    return {} if cap is None else json.loads(json.dumps(cap.fields, allow_nan=False))


def _check_pose_capability(cap, rig, *, repair=False):
    from . import body_original_mode as original
    _pose_require(type(cap) is _PoseCapability and cap.rig is rig and cap.data is rig.data,
                  'Original pose permission does not own this exact rig and data.')
    _pose_require((_same_pose_refs(rig.get(POSE_REFS), cap.refs) if cap.installed else POSE_REFS not in rig),
                  'Original pose-only native references changed during the operation.')
    rest, digest, _refs = _pose_state(cap.context, rig, cap.refs)
    _pose_require(rest == cap.rest and digest == cap.digest,
                  'Original pose-only Rest or retained history changed; keep the current Original edit.')
    if original.SESSION in rig:
        saved = json.loads(rig[original.SESSION])
        _pose_require(saved.get('pose_only_owner') == cap.fields,
                      'The Original pose-only session marker changed.')
    else:
        _pose_require(not cap.returning or cap.checkpoint is not None or cap.finished,
                      'The Original pose-only session disappeared before return.')
    if repair:
        from . import skirt_rig, limb_ik_fk
        checkpoint = cap.checkpoint
        names = {pb.name for pb in rig.pose.bones if not pb.bone.get(skirt_rig.OWNER_KEY)}
        _pose_require(type(checkpoint) is tuple and len(checkpoint) == 6
                      and isinstance(checkpoint[0], dict) and set(checkpoint[0]) == names,
                      'Original Pole repair needs its complete active outer pose checkpoint.')
        limb_ik_fk._require_existing_switches(rig)
    return True


def _display_pose_capability(cap, rig):
    if cap is None or type(cap) is not _PoseCapability or cap.rig is not rig:
        return False
    return _check_pose_capability(cap, rig)


def install_pose_session(cap):
    if cap is not None:
        _check_pose_capability(cap, cap.rig)
        _pose_require(cap.before_refs is None, 'Original pose references are already installed.')
        cap.rig[POSE_REFS] = _pose_refmap(cap.refs)
        cap.installed = True


def restore_pose_session(cap):
    if cap is not None:
        if cap.before_refs is None:
            cap.rig.pop(POSE_REFS, None)
        else:
            cap.rig[POSE_REFS] = cap.before_refs
        cap.installed, cap.finished = cap.before_refs is not None, False


def finish_pose_session(cap):
    if cap is not None:
        _check_pose_capability(cap, cap.rig)
        _pose_require(cap.returning and cap.checkpoint is not None,
                      'Original pose references may only finish inside the return transaction.')
        cap.rig.pop(POSE_REFS)
        cap.installed, cap.finished = False, True


@contextmanager
def pose_return_checkpoint(cap, checkpoint):
    if cap is None:
        yield
        return
    _check_pose_capability(cap, cap.rig)
    _pose_require(cap.returning and cap.checkpoint is None, 'An Original pose return checkpoint is already active.')
    cap.checkpoint = checkpoint
    try:
        yield
    finally:
        cap.checkpoint = None


def _saved_rest(value, label):
    from . import unity_animation as ua
    if ua._compile_saved_rest_state(value) is None or not value:
        raise ValueError(label + ' is not a complete finite armature Rest record.')
    flags = ('use_connect', 'use_deform', 'use_inherit_rotation',
             'use_local_location', 'use_relative_parent')
    for name, row in value.items():
        if (not name or any(type(row[key]) is not bool for key in flags)
                or not isinstance(row['inherit_scale'], str) or not row['inherit_scale']
                or row['parent'] is not None and row['parent'] not in value
                or any(type(number) not in (int, float) or not math.isfinite(number)
                       for key in ('head', 'tail', 'matrix') for number in row[key])):
            raise ValueError(label + ' contains an invalid native Rest row.')
    return value


def _preceding_rest(rig, saved, current):
    """Reconstruct only geometry recorded before this genuine Original edit."""
    from . import body_original_mode as original
    original._validate_session_rest(rig, saved, current=saved.get('rest'))
    result = json.loads(json.dumps(current, allow_nan=False))
    for name, row in saved['rest'].items():
        matrix = Matrix(row['matrix'])
        head = matrix.translation
        tail = head + matrix.to_3x3().col[1] * row['length']
        result[name].update(parent=row['parent'], head=list(head), tail=list(tail),
            matrix=[value for part in row['matrix'] for value in part],
            use_connect=row['connected'], inherit_scale=row['inherit_scale'],
            use_inherit_rotation=row['inherit_rotation'], use_local_location=row['local_location'])
    return _saved_rest(result, 'Saved Original')


def _frame(row):
    """Canonical EditBone geometry: head/tail axis and its perpendicular Z roll."""
    matrix = Matrix([row['matrix'][i:i + 4] for i in range(0, 16, 4)])
    if (any(not math.isfinite(value) for part in matrix for value in part)
            or abs(matrix.determinant()) < 1e-8
            or tuple(matrix[3]) != (0.0, 0.0, 0.0, 1.0)):
        raise ValueError('An owned Foot reference matrix is singular or non-finite.')
    head, tail = Vector(row['head']), Vector(row['tail'])
    axis = tail - head
    if axis.length < 1e-8 or (matrix.translation - head).length > _FRAME_EPSILON:
        raise ValueError('An owned Foot reference has no consistent native geometry.')
    y_axis = axis.normalized()
    z_axis = matrix.to_3x3().col[2]
    z_axis = z_axis - y_axis * z_axis.dot(y_axis)
    if z_axis.length < 1e-8:
        raise ValueError('An owned Foot reference has no usable roll axis.')
    z_axis.normalize()
    basis = Matrix((y_axis.cross(z_axis), y_axis, z_axis)).transposed()
    # Only the redundant X column may carry the small non-orthogonal residue
    # produced by a native Edit Mode roundtrip. Y/Z geometry stays at the
    # ordinary 1e-6 Rest precision; neither shear nor arbitrary scale is waived.
    x_error = max(abs(matrix[i][0] - basis[i][0]) for i in range(3))
    if (x_error > _FRAME_X_RESIDUAL_LIMIT
            or any(abs(matrix[i][j] - basis[i][j]) > _FRAME_EPSILON
                   for i in range(3) for j in (1, 2))):
        raise ValueError('An owned Foot reference exceeds its bounded native X-column residue.')
    return matrix, basis, x_error


def _reference_roundtrip(old, current, footprint):
    """Prove one already ownership-validated Foot reference, without writes."""
    from . import unity_animation as ua
    if footprint['role'] not in _FOOT_REFERENCE_ROLES:
        return None
    state = footprint['state']
    # Retain ordinary full-row checks for every field except the matrix; in
    # particular this keeps exact parent/connect/deform/inheritance flags.
    probe = dict(current, matrix=old['matrix'])
    if not ua._matches_current_rest_state({'bone': probe}, {'bone': old}):
        return None
    if state['parent'] != old['parent']:
        return None
    recorded = {'head': state['head'], 'tail': state['tail'],
                'matrix': [value for part in state['matrix'] for value in part]}
    if any(abs(a - b) > _FRAME_EPSILON for key in ('head', 'tail')
           for a, b in zip(recorded[key], old[key])):
        return None
    try:
        frames = [_frame(row) for row in (old, current, recorded)]
    except (ValueError, TypeError, KeyError):
        return None
    matrices, bases, x_errors = zip(*frames)
    # The old Action, live projected skeleton and immutable Foot footprint
    # independently agree on the same translation, Y axis and perpendicular Z.
    translation_error = max((mat.translation - matrices[0].translation).length
                            for mat in matrices[1:])
    # Compare BOTH endpoints independently with the immutable Foot footprint.
    # A native roundtrip can leave opposite X-column residues, so their raw
    # pairwise matrix delta is not the residue bound. The fixed third baseline
    # prevents repeated accepted returns from accumulating orientation drift.
    basis_error = max(abs(basis[i][j] - bases[2][i][j])
                      for basis in bases[:2] for i in range(3) for j in range(3))
    pairwise_basis_error = max(abs(bases[0][i][j] - bases[1][i][j])
                               for i in range(3) for j in range(3))
    matrix_error = max(abs(a - b) for a, b in zip(old['matrix'], current['matrix']))
    if (translation_error > _FRAME_EPSILON or basis_error > _FRAME_EPSILON
            or any(abs(matrix[i][j] - matrices[2][i][j]) > _FRAME_EPSILON
                   for matrix in matrices[:2] for i in range(3) for j in (1, 2))):
        return None
    return {'role': footprint['role'], 'matrix_error': matrix_error,
            'canonical_basis_error': basis_error, 'translation_error': translation_error,
            'pairwise_canonical_basis_error': pairwise_basis_error,
            'maximum_x_column_residue': max(x_errors)}


def _match_preceding(preceding, prior, footprints):
    """Ordinary full proof first; only two exact Foot roles have a fallback."""
    from . import unity_animation as ua
    if ua._matches_current_rest_state(preceding, ua._compile_saved_rest_state(prior)):
        return []
    if preceding.keys() != prior.keys():
        return None
    accepted = []
    for name, now in preceding.items():
        old = prior[name]
        if ua._matches_current_rest_state({name: now}, {name: old}):
            continue
        footprint = footprints.get(name)
        result = _reference_roundtrip(old, now, footprint) if footprint else None
        if result is None:
            return None
        accepted.append(dict(result, bone=name))
    return accepted


def _action_proof(rig, action, preceding, footprints):
    from . import animation_worklist_target as target, unity_animation as ua, limb_ik, skirt_rig
    try:
        raw = action[ua.ORIGINAL_REST_KEY]
        prior = _saved_rest(target._decode(json.loads(raw), {}), 'Retained Action artist Rest')
        proof = target._decode(json.loads(action[target.ACTION_MOTION_KEY]), {})
        motion = action[ua.PREVIEW_DATA_KEY]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('A retained Action has no intact original motion-data proof.') from error
    if (type(proof) is not dict or set(proof) != {'version', 'mapped_names', 'motion_rest',
            'original_rest_sha256'} or type(proof['version']) is not int or proof['version'] != 1
            or hashlib.sha256(raw.encode('utf-8')).hexdigest() != proof['original_rest_sha256']
            or not isinstance(motion, bpy.types.Armature) or motion.library or motion.override_library
            or motion is rig.data):
        raise ValueError('A retained Action motion-data proof changed or belongs to another model.')
    # The same complete comparison as ordinary Worklist recovery.  Only the
    # current native geometry was projected back from this Original session;
    # generated bones, flags and all other native bones remain live inputs.
    roundtrips = _match_preceding(preceding, prior, footprints)
    if roundtrips is None:
        raise ValueError('The retained Action does not authenticate this saved Original skeleton.')
    names = frozenset(target._validated_names(proof['mapped_names']))
    expected_motion = _saved_rest(proof['motion_rest'], 'Retained Action motion Rest')
    current_motion = ua._read_matched_rest_state(motion, ua._compile_saved_rest_state(expected_motion))
    if (current_motion is None or current_motion.keys() != prior.keys()
            or not names.issubset(prior)):
        raise ValueError('A retained motion data block changed; its historical proof was preserved.')
    for name, row in current_motion.items():
        old = prior[name]
        if not ua._same_current_rest_bone(old, row):
            raise ValueError('A retained motion copy changed its original skeleton: ' + name)
        if row['use_connect'] != old['use_connect']:
            bone, source = motion.bones[name], rig.data.bones[name]
            if (not old['use_connect'] or row['use_connect'] or name not in names
                    or any(source.get(key) is not None or bone.get(key) is not None
                           for key in (limb_ik.OWNER_KEY, skirt_rig.OWNER_KEY))):
                raise ValueError('A retained motion copy changed an unapproved bone connection.')
    return roundtrips


def _animation_fingerprint(action):
    from . import body_region, limb_ik
    curves = [(curve.data_path, curve.array_index, curve.extrapolation,
               [(tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                 point.interpolation, point.handle_left_type, point.handle_right_type,
                 point.easing, point.type) for point in curve.keyframe_points])
              for curve in limb_ik._fcurves_for_action(action)]
    return body_region._hash([dict(action.items()), curves])


def _admit(context, rig):
    """Read-only exact owner admission, before the ordinary editable guard."""
    from . import (animation_worklist_target as target, body_original_mode as original,
                   body_rest_resync as resync, forearm_original_inventory,
                   body_region, limb_ik, limb_ik_fk, unity_animation as ua)
    if (rig.library or rig.override_library or rig.data.library or rig.data.override_library
            or context.mode not in {'OBJECT', 'POSE'} or target.is_adopted(rig)
            or not target.managed(rig) or not original.active(rig)
            or body_region.RECORD_KEY in rig.data):
        raise ValueError('Retained animation admission requires this restored Main Rig\'s active Original session.')
    if context.screen and context.screen.is_animation_playing:
        raise ValueError('Pause playback before returning edited Original bones to Controls.')
    raw = rig.get(original.SESSION)
    try:
        saved = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise ValueError('The saved Original session is unreadable; preserve the current edit.') from error
    current_native, changed = resync._geometry_changes(rig, saved)
    if not changed:
        raise ValueError('An unchanged Original skeleton does not explain the failed recovery-owner proof.')
    view = resync.OriginalRestView(rig, saved, current_native, changed)
    inventory = forearm_original_inventory.validate(rig, original_rest=view)
    if inventory['schema'] != limb_ik.DIRECT_PREROLL_SCHEMA:
        raise ValueError('Retained Original Rest admission requires current Direct controls.')
    allowed = {name for key, entry in inventory['rigs'].items()
               for name in (limb_ik_fk.pose_names(entry) if key[0] == 'LEG' else entry['chain'][:2])}
    if not changed.issubset(allowed):
        raise ValueError('Original edits extend beyond existing Direct limb source geometry.')
    # _recovery_owners checks actual native ID users, exact local Action/rigger
    # references, distinct local motion data, and rejects every foreign user.
    owners = body_region._recovery_owners(rig)
    current = _saved_rest(ua._rest_state(rig.data), 'Current artist Rest')
    preceding = _preceding_rest(rig, saved, current)
    footprints = {}
    for key, entry in inventory['rigs'].items():
        foot = entry.get('foot_controls')
        if key[0] != 'LEG' or not foot or not changed.intersection(limb_ik_fk.pose_names(entry)):
            continue
        for role in _FOOT_REFERENCE_ROLES:
            name = foot['bones'].get(role)
            if name is not None:
                footprints[name] = {'role': role, 'state': foot['bone_states'][role]}
    roundtrips = []
    if body_region.RECOVERY_OWNER_KEY in rig.data:
        # Later intentional edits start from the last accepted model revision,
        # while historical clips deliberately keep their older original Rest.
        revision = body_region._owner_proof(rig)
        roundtrips = _match_preceding(preceding, revision['rest'], footprints)
        if (roundtrips is None or set(revision['bone_properties']) != set(current)
                or any(body_region._hash(body_region._bone_ownership(bone))
                       != revision['bone_properties'][bone.name] for bone in rig.data.bones)):
            raise ValueError('The last accepted model revision does not authenticate this Original session.')
    else:
        revision = None
    actions = {}
    for entry in owners['actions']:
        action = bpy.data.actions.get(entry['name'])
        if revision is None:
            accepted = _action_proof(rig, action, preceding, footprints)
            roundtrips.extend(dict(entry, action=action.name_full) for entry in accepted)
        actions[action.name_full] = _animation_fingerprint(action)
    proof = {'version': 1, 'rig': rig.name_full, 'data': rig.data.name_full,
             'armature_id': inventory['armature_id'], 'owners': owners, 'rest': current,
             'bone_properties': {bone.name: body_region._hash(body_region._bone_ownership(bone))
                                 for bone in rig.data.bones}}
    if roundtrips:
        proof['original_return_frame_roundtrips'] = {
            'version': 1, 'geometry_tolerance': _FRAME_EPSILON,
            'x_column_residue_limit': _FRAME_X_RESIDUAL_LIMIT,
            'comparison': 'Each endpoint against the immutable Foot frame; not a pairwise matrix tolerance.',
            'roles': sorted(_FOOT_REFERENCE_ROLES), 'bones': roundtrips}
    return {'proof': proof, 'session': raw, 'rig': rig, 'data': rig.data,
            'rest': current, 'actions': actions}


def run_return(context, rig, callback):
    """Return Controls with bounded ownership and a complete failure rollback."""
    from . import body_original_mode as original, body_region, limb_ik
    capability = pose_capability(context, rig)
    if capability is not None:
        def finish_pose_return():
            _check_pose_capability(capability, rig)
            _pose_require(capability.finished and not original.active(rig),
                          'Original pose-only return did not finish its authenticated session.')
        return callback(finish_pose_return, _original_pose=capability)
    if not original.active(rig) or rig.data.users == 1 or limb_ik._single_armature_user(rig):
        return callback()
    from . import unity_animation as ua
    admission = _admit(context, rig)
    previous_revision = rig.data.get(body_region.RECOVERY_OWNER_KEY)

    def commit():
        if original.active(rig) or rig.data is not admission['data']:
            raise ValueError('Original return did not complete on its authenticated artist data.')
        if ua._rest_state(rig.data) != admission['rest']:
            raise ValueError('Original return changed the edited native skeleton; no revision was accepted.')
        for name, fingerprint in admission['actions'].items():
            action = bpy.data.actions.get(name)
            if action is None or _animation_fingerprint(action) != fingerprint:
                raise ValueError('A retained animation changed during Original return.')
        body_region._finish_owner_revision(rig, admission['proof'])

    with body_region._ownership_operation(rig, admission['proof']):
        try:
            if rig.data is not admission['data'] or rig.get(original.SESSION) != admission['session']:
                raise ValueError('The admitted Original return changed identity before execution.')
            # Run inside Original's existing verified transaction. A finalizer
            # failure therefore uses its full Body/Dress/display/corrective
            # rollback, without dismantling or recreating any control graph.
            return callback(commit)
        except Exception:
            if previous_revision is None:
                rig.data.pop(body_region.RECOVERY_OWNER_KEY, None)
            else:
                rig.data[body_region.RECOVERY_OWNER_KEY] = previous_revision
            raise
