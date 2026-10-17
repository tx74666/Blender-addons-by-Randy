"""Authenticate retained Worklist references during one Original return.

The current native Rest is never repaired or replaced.  A saved Original
session supplies the preceding native geometry, while each retained Action
must independently authenticate that entire preceding armature and its exact
unchanged motion data.  This is not a generic shared-data exception.
"""
import hashlib
import json
import math

import bpy
from mathutils import Matrix, Vector


_FRAME_EPSILON = 1e-6
_FRAME_X_RESIDUAL_LIMIT = 2e-5
_FOOT_REFERENCE_ROLES = frozenset(('FK_TOE_REF', 'AUTO_TOE_REF'))


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
