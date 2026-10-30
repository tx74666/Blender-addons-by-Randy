"""Explicit regional control teardown and reattachment, without native re-rigging.

Only current Direct limbs are admitted.  Native bones, mesh binding, calibration
and shared body controls are never reconstructed.  A saved release receipt owns
the missing limb; an in-memory admission additionally owns each build call.
"""
import json
import math
import hashlib
from contextlib import contextmanager
from functools import lru_cache

import bpy
from mathutils import Vector
from . import (limb_ik, limb_ik_fk, body_setup, body_setup_removal,
               body_setup_transaction, body_calibration, foot_controls,
               limb_fk_visuals, root_control)

RECORD_KEY = 'character_designer_body_region_release_v1'
RECOVERY_OWNER_KEY = 'character_designer_body_region_recovery_owners_v1'
VERSION = 1
REGIONS = {
    'LEFT_LEG': (('LEG', 'L'),), 'RIGHT_LEG': (('LEG', 'R'),),
    'LEGS': (('LEG', 'L'), ('LEG', 'R')),
    'LEFT_ARM': (('ARM', 'L'),), 'RIGHT_ARM': (('ARM', 'R'),),
    'ARMS': (('ARM', 'L'), ('ARM', 'R')),
}
_BIND_AUTH = None
_OWNER_OPERATION = None


def _primitive(value):
    """Serialize identity references without adding native ID users."""
    if isinstance(value, bpy.types.ID):
        return {'id_type': value.bl_rna.identifier, 'name': value.name_full,
                'library': value.library.filepath if value.library else None}
    if hasattr(value, 'items'):
        return {str(k): _primitive(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, '__iter__'):
        return [_primitive(v) for v in value]
    raise ValueError('A recovery owner contains an unsupported native property.')


def _hash(value):
    return hashlib.sha256(json.dumps(_primitive(value), ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _action_owner(action):
    return {'name': action.name_full, 'properties_sha256': _hash(dict(action.items()))}


def _bone_ownership_fields():
    """Read the provenance schema once for one bounded proof, never cache it."""
    from . import widget_collections
    keys = {limb_ik.OWNER_KEY, limb_ik.VERSION_KEY, limb_ik.ARMATURE_ID_KEY,
            limb_ik.RIG_ID_KEY, limb_ik.ROLE_KEY, limb_ik.KIND_KEY,
            limb_ik.SIDE_KEY, limb_ik.CHAIN_KEY}
    for module in widget_collections._modules():
        for field in ('OWNER_KEY', 'ID_KEY', 'RIG_KEY', 'ROLE_KEY', 'SIDE_KEY'):
            if key := getattr(module, field, None):
                keys.add(key)
    return frozenset(keys)


def _bone_ownership(bone, fields=None):
    """Pin immutable provenance, while animator display/mode settings remain usable."""
    if fields is None:
        fields = _bone_ownership_fields()
    return {key: bone[key] for key in fields if key in bone}


def _bone_ownership_hashes(bones):
    fields = _bone_ownership_fields()
    return {bone.name: _hash(_bone_ownership(bone, fields)) for bone in bones}


def _recovery_owners(rig):
    from . import body_rest_adoption
    if body_rest_adoption.has_revision(rig):
        return body_rest_adoption.recovery_owners(rig)
    from . import animation_worklist_target as target, unity_animation as ua
    data = rig.data
    bound = [obj for obj in bpy.data.objects if obj.type == 'ARMATURE' and obj.data is data]
    if (len(bound) != 1 or bound[0] is not rig or rig.library or rig.override_library
            or data.library or data.override_library or target.is_adopted(rig)):
        raise ValueError('Regional editing requires one actual local armature binding outside adopted animation editing.')
    owners = bpy.data.user_map().get(data, set())
    if rig not in owners or target._data_reference_keys(rig, data):
        raise ValueError('An unknown rig property or owner refers to the regional Armature data.')
    actions = []
    motions = {}
    for owner in owners:
        if owner is rig:
            continue
        if (not isinstance(owner, bpy.types.Action) or owner.library is not None
                or owner.get(target.ACTION_RIG_KEY) is not rig
                or owner.get(ua.ORIGINAL_DATA_KEY) is not data
                or target._data_reference_keys(owner, data) - {ua.ORIGINAL_DATA_KEY, ua.PREVIEW_DATA_KEY}):
            raise ValueError('An unknown user refers to the regional Armature data; keep its existing rig intact.')
        motion = owner.get(ua.PREVIEW_DATA_KEY)
        if not isinstance(motion, bpy.types.Armature) or motion.library or motion is data:
            raise ValueError('A retained animation lost its exact separate motion data reference.')
        actions.append(_action_owner(owner))
        motions[motion.name_full] = {'rest_sha256': _hash(ua._rest_state(motion)),
                                    'properties_sha256': _hash(dict(motion.items()))}
    if not actions:
        raise ValueError('No proven retained animation owners explain the extra Armature users.')
    return {'actions': sorted(actions, key=lambda item: item['name']), 'motions': motions,
            'fake_user': bool(data.use_fake_user)}


def _owner_proof(rig):
    from . import unity_animation as ua
    raw = rig.data.get(RECOVERY_OWNER_KEY)
    if not isinstance(raw, str):
        raise ValueError('This regional model has no authenticated retained animation owner record.')
    proof = json.loads(raw)
    if (not isinstance(proof, dict) or proof.get('version') != 1
            or proof.get('rig') != rig.name_full or proof.get('data') != rig.data.name_full
            or proof.get('armature_id') != rig.data.get(limb_ik.ARMATURE_ID_KEY)
            or not isinstance(proof.get('rest'), dict)
            or not isinstance(proof.get('bone_properties'), dict)
            or ua._compile_saved_rest_state(proof['rest']) is None
            or proof.get('owners') != _recovery_owners(rig)):
        raise ValueError('Retained animation owners or the regional model identity changed; no shared-data exception was granted.')
    return proof


def verified_single_armature_user(rig):
    """Bounded sharing proof; never makes old animation Rest proof current.

    A proof is created only after the original Worklist owner/Rest admission.
    Known Action references remain untouched.  Pending edits may vary only the
    released native geometry; completed revisions pin the entire new Rest.
    """
    from . import body_rest_adoption
    if body_rest_adoption.active_owner(rig):
        return True
    try:
        active = _OWNER_OPERATION
        if active is not None and active['rig'] is rig and active['data'] is rig.data:
            return active['proof']['owners'] == _recovery_owners(rig)
        proof = _owner_proof(rig)
        entries = _load(rig)
        # A completed revision grants no Rest exemptions. Its exact native
        # proof also remains usable while Original temporarily mutes controls;
        # only an actual open receipt needs the ordinary control inventory.
        allowed = _validate_receipts(rig, entries) if entries else set()
        from . import unity_animation as ua
        current = ua._rest_state(rig.data)
        if current.keys() != proof['rest'].keys() or set(proof['bone_properties']) != set(current):
            return False
        fields = _bone_ownership_fields()
        relations = ('parent', 'use_connect', 'use_deform', 'use_inherit_rotation',
                     'inherit_scale', 'use_local_location', 'use_relative_parent')
        for name, now in current.items():
            old = proof['rest'][name]
            if any(now[k] != old[k] for k in relations):
                return False
            if name not in allowed and not ua._same_current_rest_bone(old, now):
                return False
            if _hash(_bone_ownership(rig.data.bones[name], fields)) != proof['bone_properties'][name]:
                return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError, ReferenceError, RecursionError):
        return False


def _ownership_seed(rig):
    if rig.data.users == 1:
        return None
    from . import animation_worklist_target as target, unity_animation as ua
    if target.single_armature_user(rig):
        # This is the only entry from the old complete artist+motion Rest proof.
        return {'version': 1, 'rig': rig.name_full, 'data': rig.data.name_full,
                'armature_id': rig.data.get(limb_ik.ARMATURE_ID_KEY),
                'owners': _recovery_owners(rig), 'rest': ua._rest_state(rig.data),
                'bone_properties': _bone_ownership_hashes(rig.data.bones)}
    if not verified_single_armature_user(rig):
        raise ValueError('Retained animation recovery owners no longer authenticate this regional model.')
    return _owner_proof(rig)


@contextmanager
def _ownership_operation(rig, proof):
    global _OWNER_OPERATION
    if proof is None:
        yield
        return
    if _OWNER_OPERATION is not None:
        raise ValueError('Another regional ownership transaction is active.')
    if proof['owners'] != _recovery_owners(rig):
        raise ValueError('Retained animation recovery owners changed before the regional transaction.')
    _OWNER_OPERATION = {'rig': rig, 'data': rig.data, 'proof': proof}
    try:
        yield
    finally:
        _OWNER_OPERATION = None


def _finish_owner_revision(rig, proof):
    if proof is None:
        return
    from . import animation_worklist_target as target, unity_animation as ua
    if proof['owners'] != _recovery_owners(rig):
        raise ValueError('A retained animation owner changed during regional editing; the operation was rolled back.')
    current = dict(proof, rest=ua._rest_state(rig.data),
                   bone_properties=_bone_ownership_hashes(rig.data.bones))
    stale = []
    for owner in proof['owners']['actions']:
        action = bpy.data.actions.get(owner['name'])
        try:
            target._read_action_motion_data(rig, action, rig.data)
        except (ValueError, TypeError, KeyError, AttributeError):
            stale.append(owner['name'])
    current['stale_actions'] = stale
    rig.data[RECOVERY_OWNER_KEY] = json.dumps(current, sort_keys=True, ensure_ascii=False,
                                           separators=(',', ':'), allow_nan=False)


def _keys(region):
    try:
        return REGIONS[region]
    except KeyError as exc:
        raise ValueError('Choose left/right leg or arm, or both legs/arms.') from exc


def _key(key):
    return ':'.join(key)


def _finite_rest_rows(rows):
    if not isinstance(rows, dict):
        raise ValueError('invalid saved native Rest')
    for name, state in rows.items():
        if (not isinstance(name, str) or not name or not isinstance(state, dict)
                or not isinstance(state.get('parent'), str)
                or type(state.get('use_connect')) is not bool
                or type(state.get('use_deform')) is not bool):
            raise ValueError('invalid saved native Rest')
        for field in ('head', 'tail', 'z'):
            vector = state.get(field)
            if (not isinstance(vector, (list, tuple)) or len(vector) != 3
                    or any(type(number) not in (int, float) or not math.isfinite(number)
                           for number in vector)):
                raise ValueError('invalid or non-finite saved native Rest')


def _decode_receipt(raw):
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value['version'] != VERSION or not isinstance(value['limbs'], dict):
            raise ValueError('invalid version')
        for name, entry in value['limbs'].items():
            if (not isinstance(entry, dict) or name not in {_key(k) for pairs in REGIONS.values() for k in pairs}
                    or entry['key'] != name or type(entry['schema']) is not int
                    or entry['schema'] != limb_ik.DIRECT_PREROLL_SCHEMA
                    or not isinstance(entry['armature_id'], str) or not entry['armature_id']
                    or type(entry['feature']) is not int or entry['feature'] not in {0, limb_ik.TARGET_ROTATION_VERSION}
                    or type(entry['auto_align']) is not bool
                    or not isinstance(entry['chain'], list) or len(entry['chain']) != 3
                    or any(not isinstance(n, str) or not n for n in entry['chain']) or len(set(entry['chain'])) != 3
                    or not isinstance(entry['allowed'], list)
                    or any(not isinstance(n, str) or not n for n in entry['allowed'])
                    or len(set(entry['allowed'])) != len(entry['allowed'])
                    or not isinstance(entry['relations'], dict)
                    or type(entry['rings']) is not bool or type(entry['source_shapes']) is not bool
                    or not isinstance(entry['source_names'], list)
                    or not isinstance(entry['protected_rest'], dict)):
                raise ValueError('invalid limb receipt')
            _finite_rest_rows(entry['relations'])
            _finite_rest_rows(entry['protected_rest'])
        return value['limbs']
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError('Regional release receipt is invalid; keep this character intact.') from exc


def _load(rig):
    # Each operation owns fresh mutable input, independently of the draw cache.
    return _decode_receipt(rig.data.get(RECORD_KEY))


@lru_cache(maxsize=2)
def _receipt_keys(raw):
    # Cache only immutable summaries.  Reading UI state never shares mutable
    # operation receipts or reparses the full protected Rest on every redraw.
    try:
        return frozenset(_decode_receipt(raw)), ''
    except (ValueError, TypeError) as exc:
        return frozenset(), str(exc)


@lru_cache(maxsize=2)
def _stale_animation_notice(raw):
    try:
        proof = json.loads(raw) if isinstance(raw, str) else {}
        names = proof.get('stale_actions', ())
        return bool(isinstance(names, list) and names and all(isinstance(n, str) for n in names))
    except (ValueError, TypeError, AttributeError):
        return False


def _save(rig, entries):
    if entries:
        rig.data[RECORD_KEY] = json.dumps({'version': VERSION, 'limbs': entries},
                                        sort_keys=True, separators=(',', ':'), allow_nan=False)
    else:
        rig.data.pop(RECORD_KEY, None)


def _validate_receipts(rig, entries):
    """Authenticate every open scope before exempting it from Rest protection."""
    native = body_calibration.native_rest(rig)
    scopes = set()
    inventory = limb_ik._validate_inventory(rig)
    bound_chains = {n for data in inventory['rigs'].values() for n in data['chain']}
    for name, entry in entries.items():
        kind, side = name.split(':')
        chain = entry['chain']
        if (entry['armature_id'] != rig.data.get(limb_ik.ARMATURE_ID_KEY)
                or inventory['schema'] != entry['schema']
                or (inventory['rigs'] and inventory['target_rotation_version'] != entry['feature'])
                or (kind, side) in inventory['rigs'] or set(chain) & bound_chains
                or any(n not in native for n in chain)):
            raise ValueError('A regional receipt belongs to another rig, solver or native chain; keep it intact.')
        upper, lower, end = (rig.data.bones[n] for n in chain)
        if (lower.parent != upper or end.parent != lower or not all(b.use_deform for b in (upper, lower, end))
                or any(limb_ik._side_from_name(n) not in {None, '', side} for n in chain)):
            raise ValueError('A released chain no longer has its saved native hierarchy or side.')
        allowed = set(entry['allowed'])
        if (allowed != _native_scope(rig, chain) or scopes & allowed
                or set(entry['relations']) != allowed
                or set(entry['protected_rest']) & allowed
                or set(entry['protected_rest']) | allowed != set(native)):
            raise ValueError('A released native scope no longer matches its protected Rest partition.')
        candidates = limb_ik._source_widget_candidates(rig, [limb_ik.LimbChain(kind, side, *chain)])
        shape_names = {n for values in candidates.values() for n, _side in values}
        if (set(entry['source_names']) - shape_names
                or (not entry['source_shapes'] and entry['source_names'])):
            raise ValueError('A regional source-display receipt includes unrelated native controls.')
        scopes.update(allowed)
        for bone_name in allowed:
            before = entry['relations'][bone_name]
            if (not isinstance(before, dict)
                    or any(native[bone_name][k] != before.get(k) for k in ('parent', 'use_connect', 'use_deform'))):
                raise ValueError('Native relationships changed; regional rebuild accepts Rest geometry edits only.')
        if (not isinstance(entry.get('pole_distance'), (int, float))
                or not math.isfinite(entry['pole_distance']) or entry['pole_distance'] <= 0):
            raise ValueError('The released Pole distance is invalid.')
        if 'pole_layout' in entry:
            _validate_pole_layout(entry['pole_layout'])
    return scopes


def _validate_pole_layout(layout):
    if (not isinstance(layout, dict) or type(layout.get('version')) is not int
            or layout['version'] != 1):
        raise ValueError('The released Pole layout is invalid.')
    for name in ('axis', 'direction'):
        values = layout.get(name)
        if (not isinstance(values, list) or len(values) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
                or abs(Vector(values).length - 1.0) > 1e-5):
            raise ValueError('The released Pole layout has an invalid direction.')
    if abs(Vector(layout['axis']).dot(Vector(layout['direction']))) > 1e-5:
        raise ValueError('The released Pole layout is not perpendicular to its chain.')
    for name in ('axial_ratio', 'radial_ratio'):
        value = layout.get(name)
        if (type(value) not in (int, float) or not math.isfinite(value)
                or abs(value) > 3.4028234663852886e38):
            raise ValueError('The released Pole layout has an invalid distance.')
    if layout['radial_ratio'] <= 1e-8:
        raise ValueError('The released Pole layout has no radial distance.')


def _capture_pole_layout(rig, key, data):
    """Separate controller layout from the native Rest bend and source roll.

    Legs use their stored configured direction: an earlier pose-matching
    compensation must not silently place the right knee Pole behind the body.
    Arms retain their actual layout. Both keep the authored distance and height.
    """
    start, joint, end = (rig.pose.bones[n].matrix.translation.copy() for n in data['chain'])
    axis = end - start
    total = (joint - start).length + (end - joint).length
    if axis.length <= limb_ik.EPSILON or total <= limb_ik.EPSILON:
        raise ValueError('The released limb has no usable Pole layout axis.')
    axis.normalize()
    offset = rig.pose.bones[data['pole'].name].matrix.translation - start
    radial = limb_ik._project_perpendicular(offset, axis)
    direction = radial.copy()
    if key[0] == 'LEG':
        configured = data['configured_pole_direction'] or data['pole_direction']
        front = limb_ik_fk._leg_front_direction(axis)
        direction = limb_ik._project_perpendicular(configured, axis) if configured is not None else front
        if direction.length <= limb_ik.EPSILON or direction.normalized().dot(front) < 0.8:
            direction = front
    if radial.length <= limb_ik.EPSILON or direction.length <= limb_ik.EPSILON:
        raise ValueError('The released Pole has no stable layout direction.')
    layout = {'version': 1, 'axis': list(axis), 'direction': list(direction.normalized()),
              'axial_ratio': offset.dot(axis) / total, 'radial_ratio': radial.length / total}
    _validate_pole_layout(layout)
    return layout


def layout_poles(rig, plans, desired_pose):
    """Refit generated Rest frames after the native calibration admission."""
    authorize_build(rig, plans)
    entries = _BIND_AUTH['entries']
    fixed = set()
    for plan in plans:
        entry = entries[_key((plan.chain.kind, plan.chain.side))]
        layout = entry.get('pole_layout')
        if layout is None and plan.chain.kind != 'LEG':
            # Receipts saved by older versions contain no authored layout.
            # Their new controls retain the old placement/matching behavior.
            continue
        start, joint, end = (desired_pose[n].translation for n in plan.chain.names)
        axis = end - start
        total = (joint - start).length + (end - joint).length
        if axis.length <= limb_ik.EPSILON or total <= limb_ik.EPSILON:
            raise ValueError('The rebuilt limb has no usable Pole layout axis.')
        axis.normalize()
        if layout is None:
            # Older receipts must not revive the old roll-compensation path.
            # Their native geometry and saved distance still define a usable
            # front layout without deleting or re-calibrating original bones.
            offset = plan.pole_head - start
            radial = limb_ik._project_perpendicular(offset, axis)
            layout = {'version': 1, 'axis': list(axis),
                      'direction': list(limb_ik_fk._leg_front_direction(axis)),
                      'axial_ratio': offset.dot(axis) / total,
                      'radial_ratio': max(radial.length / total, entry['pole_distance'])}
        _validate_pole_layout(layout)
        direction = Vector(layout['axis']).rotation_difference(axis) @ Vector(layout['direction'])
        direction = limb_ik._project_perpendicular(direction, axis)
        if direction.length <= limb_ik.EPSILON:
            raise ValueError('The rebuilt Pole layout is parallel to its limb.')
        direction.normalize()
        if plan.chain.kind == 'LEG':
            front = limb_ik_fk._leg_front_direction(axis)
            if direction.dot(front) < 0.8:
                direction = front
        position = start + axis * (layout['axial_ratio'] * total) + direction * (layout['radial_ratio'] * total)
        if any(not math.isfinite(value) for value in position):
            raise ValueError('The rebuilt Pole layout is outside the supported coordinate range.')
        size = (plan.pole_tail - plan.pole_head).length
        plan.pole_head = position
        plan.pole_tail = position + direction * size
        plan.pole_joint = joint.copy()
        plan.pole_direction = direction
        plan.configured_pole_direction = direction.copy()
        fixed.add((plan.chain.kind, plan.chain.side))
    return fixed


def position_poles(context, rig, plans, fixed):
    """Retain the accepted Armature-local point even under a posed Root."""
    authorize_build(rig, plans)
    for plan in plans:
        if (plan.chain.kind, plan.chain.side) not in fixed:
            continue
        pole = rig.pose.bones[plan.pole_name]
        matrix = pole.matrix.copy()
        matrix.translation = plan.pole_head
        limb_ik_fk._set_matrix(context, rig, pole, matrix)


def state(rig, region):
    """Cheap draw-time status: saved receipt and owned tags, never graph audits."""
    try:
        keys = _keys(region)
        if rig is None or rig.type != 'ARMATURE':
            return {'status': 'MISSING', 'label': 'Select Main Rig', 'reason': 'Select the character armature.'}
        released, error = _receipt_keys(rig.data.get(RECORD_KEY))
        if error:
            raise ValueError(error)
        bound = {(b.get(limb_ik.KIND_KEY), b.get(limb_ik.SIDE_KEY))
                 for b in rig.data.bones if b.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                 and b.get(limb_ik.ROLE_KEY) in {'HAND_IK', 'FOOT_IK'}}
        values = [('RELEASED' if _key(k) in released else 'BOUND' if k in bound else 'MISSING') for k in keys]
        if any(_key(k) in released and k in bound for k in keys):
            raise ValueError('A released region also has controls; inspect its ownership before continuing.')
        status = values[0] if len(set(values)) == 1 else 'MIXED'
        reason = {'BOUND': 'Release only these generated controls; keep native bones and other body regions.',
                  'RELEASED': 'Edit these native bones, then rebuild only this released region.',
                  'MISSING': 'This region has no owned controls or saved release.',
                  'MIXED': 'The two sides have different states; choose a single side.'}[status]
        if _stale_animation_notice(rig.data.get(RECOVERY_OWNER_KEY)):
            reason += ' Saved animations retain earlier body proportions; check target compatibility before reuse.'
        label = {'LEFT_LEG': 'Left Leg', 'RIGHT_LEG': 'Right Leg', 'LEGS': 'Both Legs',
                 'LEFT_ARM': 'Left Arm', 'RIGHT_ARM': 'Right Arm', 'ARMS': 'Both Arms'}[region]
        return {'status': status, 'label': label, 'reason': reason}
    except (ValueError, TypeError, ReferenceError, AttributeError) as exc:
        return {'status': 'ERROR', 'label': 'Check Region', 'reason': str(exc)}


def _require(context, rig):
    from . import animation_worklist_target, body_original_mode
    body_setup._require_rig(context, rig)
    if body_original_mode.SESSION in rig:
        raise ValueError('Choose Controls before releasing a region; Original is a temporary display session.')
    from . import body_rest_adoption
    current_controls = (body_rest_adoption.has_revision(rig)
                        and verified_single_armature_user(rig))
    if animation_worklist_target.is_adopted(rig) and not current_controls:
        raise ValueError('Restore Main Rig in Animation > Advanced before changing native bone proportions.')
    if context.screen and context.screen.is_animation_playing:
        raise ValueError('Pause playback before changing regional controls.')
    # Resolve pending authored inputs before reading native pose matrices and
    # evaluated surfaces, so both baselines describe the same current pose.
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()


def _native_scope(rig, chain):
    names = set(chain)
    end = rig.data.bones[chain[-1]]
    names.update(b.name for b in end.children_recursive
                 if b.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS)
    return names


def _guard(rig, excluded):
    result = {}
    for bone in rig.data.bones:
        if bone.name in excluded:
            continue
        saved = body_setup_transaction._bone(rig, bone)
        # Mode changes legitimately alter selection and display hiding.  Raw
        # geometry, native inputs, shapes, constraints and ownership stay exact.
        for key in ('select', 'select_head', 'select_tail', 'hide'):
            saved['bone'].pop(key, None)
        saved['pose'].pop('hide', None)
        result[bone.name] = saved
    return result


def _verify_guard(rig, before, excluded):
    after = _guard(rig, excluded)
    if after != before:
        names = sorted(set(before) | set(after))
        name = next((n for n in names if before.get(n) != after.get(n)), 'unknown')
        raise ValueError(f'Regional operation changed protected bone {name}; the operation was rolled back.')


def _path_mentions(path, names):
    for name in names:
        quoted = limb_ik._rna_escape(name)
        for prefix in ('pose.bones', 'data.bones', 'bones'):
            if f'{prefix}["{quoted}"]' in path:
                return True
    return False


def _animations(rig, names, driver_paths=()):
    """Reject actual affected paths, retaining independent arm/leg animation."""
    for owner in (rig, rig.data):
        for action in limb_ik._actions_for_id(owner):
            for curve in limb_ik._fcurves_for_action(action):
                if _path_mentions(curve.data_path, names):
                    raise ValueError('The selected region has authored animation; preserve/bake it before releasing or rebuilding its controls.')
        for curve in owner.animation_data.drivers if owner.animation_data else ():
            if owner is rig and curve.data_path in driver_paths:
                continue
            if _path_mentions(curve.data_path, names):
                raise ValueError('An authored driver writes to the selected region; preserve that dependency first.')


def _dependencies(rig, names, constraints, driver_paths):
    for bone in rig.data.bones:
        if bone.name not in names and bone.parent and bone.parent.name in names:
            raise ValueError(f'Protected bone {bone.name} follows a controller being released.')
    for obj in bpy.data.objects:
        if obj.parent == rig and obj.parent_type == 'BONE' and obj.parent_bone in names:
            raise ValueError(f'Object {obj.name} follows a controller being released.')
        for con in obj.constraints:
            if limb_ik._constraint_references_controls(con, rig, names):
                raise ValueError(f'An object constraint on {obj.name} follows the selected controls.')
        if obj.type != 'ARMATURE':
            continue
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if obj is rig and (pb.name, con.name) in constraints:
                    continue
                if (obj is rig and pb.name in names) or limb_ik._constraint_references_controls(con, rig, names):
                    raise ValueError(f'An external constraint on {pb.name} uses the selected controls.')
            if obj is rig and pb.name not in names and pb.custom_shape_transform and pb.custom_shape_transform.name in names:
                raise ValueError(f'Protected display {pb.name} uses a selected controller frame.')
    for owner in foot_controls._driver_owners():
        for curve in owner.animation_data.drivers if getattr(owner, 'animation_data', None) else ():
            if owner is rig and curve.data_path in driver_paths:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id in (rig, rig.data) and (target.bone_target in names or _path_mentions(target.data_path, names)):
                        raise ValueError('An external driver reads the selected controls; preserve that dependency first.')


def _required_widget(kind, remaining):
    return bool(remaining and kind == 'POLE_ARROW'
                or any(k == 'ARM' for k, _s in remaining) and kind == 'HAND'
                or any(k == 'LEG' for k, _s in remaining) and kind == 'FOOT'
                or kind in {'FOOT_L', 'FOOT_R'} and ('LEG', kind[-1]) in remaining
                or remaining and kind in limb_ik.CONTROL_SHAPE_WIDGET_KINDS)


def _widget_candidates(rig, inventory, keys, removed_names):
    """Predict exactly the owned leaves that the selected teardown can delete."""
    selected = set(removed_names)
    selected.update(n for n, e in (inventory['source_widgets'] or {}).get('bones', {}).items()
                    if ('ARM', e['side']) in keys)
    result = set()
    rings = limb_fk_visuals.get_record(rig)
    if rings:
        result.update(bpy.data.objects[e['object']] for e in rings['bindings'].values()
                      if (e['kind'], e['side']) in keys)
    for key in keys:
        foot = inventory['rigs'][key].get('foot_controls')
        if foot:
            result.update(bpy.data.objects[e['object']] for e in foot['widgets'].values())
    remaining = set(inventory['rigs']) - keys
    for obj in bpy.data.objects:
        if not limb_ik._owned(obj, inventory['armature_id'], role='WIDGET'):
            continue
        bindings = [(other, pb.name) for other in bpy.data.objects if other.type == 'ARMATURE'
                    for pb in other.pose.bones if pb.custom_shape == obj]
        if any(other != rig or name not in selected for other, name in bindings):
            continue
        if not _required_widget(obj.get(limb_ik.KIND_KEY), remaining):
            result.add(obj)
    return result


def _check_widget_dependencies(objects):
    if not objects:
        return
    bindings = {widget: sum(pb.custom_shape == widget for obj in bpy.data.objects
                           if obj.type == 'ARMATURE' for pb in obj.pose.bones) for widget in objects}
    for widget in objects:
        if (widget.users != len(widget.users_collection) + bindings[widget]
                or widget.parent or widget.constraints or widget.modifiers
                or widget.animation_data or widget.data.animation_data or widget.data.users != 1):
            raise ValueError(f'Widget {widget.name} has artist data or external users; preserve that use before releasing controls.')
    for obj in bpy.data.objects:
        if obj.parent in objects:
            raise ValueError(f'Object {obj.name} follows a selected control widget; preserve that attachment first.')
        constraint_owners = [obj]
        if obj.type == 'ARMATURE':
            constraint_owners.extend(obj.pose.bones)
        for owner in constraint_owners:
            for con in owner.constraints:
                for prop in con.bl_rna.properties:
                    if prop.type == 'POINTER' and getattr(con, prop.identifier, None) in objects:
                        raise ValueError('An external constraint uses a selected control widget; preserve that attachment first.')
    for owner in foot_controls._driver_owners():
        for curve in owner.animation_data.drivers if getattr(owner, 'animation_data', None) else ():
            for variable in curve.driver.variables:
                if any(target.id in objects for target in variable.targets):
                    raise ValueError('An external driver reads a selected control widget; preserve that dependency first.')


def _prune_displays(rig, keys, names):
    rings = limb_fk_visuals.get_record(rig)
    if rings:
        chosen = {name: entry for name, entry in rings['bindings'].items()
                  if (entry['kind'], entry['side']) in keys}
        if chosen:
            collection = bpy.data.collections[rings['collection']]
            if (collection.children or collection.users > 1
                    or set(collection.objects.keys()) != {e['object'] for e in rings['bindings'].values()}):
                raise ValueError('The FK ring collection contains artist data or is shared.')
            objects = {entry['object']: name for name, entry in chosen.items()}
            for name, entry in chosen.items():
                obj = bpy.data.objects[entry['object']]
                if (obj.data.users != 1 or obj.users > 2 or tuple(obj.users_collection) != (collection,)
                        or obj.animation_data is not None or obj.data.animation_data is not None
                        or limb_fk_visuals._animated_display(rig, rig.pose.bones[name])):
                    raise ValueError('A selected FK ring has artist animation or shared data; preserve it first.')
            for obj in bpy.data.objects:
                if obj.type == 'ARMATURE':
                    for pb in obj.pose.bones:
                        if (pb.custom_shape and pb.custom_shape.name in objects
                                and (obj != rig or pb.name != objects[pb.custom_shape.name])):
                            raise ValueError('A selected FK ring is used by another bone; preserve it first.')
            for name, entry in chosen.items():
                limb_ik._restore_pose_shape_state(rig, rig.pose.bones[name], entry['original'])
            limb_fk_visuals._delete_resources(rings, chosen)
            rings['bindings'] = {n: e for n, e in rings['bindings'].items() if n not in chosen}
            native = json.loads(rig.data.get(limb_fk_visuals.NATIVE_DISPLAY_KEY, '{}'))
            native = {n: e for n, e in native.items() if n not in chosen}
            if native:
                rig.data[limb_fk_visuals.NATIVE_DISPLAY_KEY] = json.dumps(native)
            else:
                rig.data.pop(limb_fk_visuals.NATIVE_DISPLAY_KEY, None)
            if rings['bindings']:
                rig.data[limb_fk_visuals.RECORD_KEY] = json.dumps(rings)
            else:
                rig.data.pop(limb_fk_visuals.RECORD_KEY, None)
    sizes = limb_fk_visuals._size_record(rig)
    if sizes:
        sizes['bindings'] = {n: e for n, e in sizes['bindings'].items() if n not in names}
        if sizes['bindings']:
            rig.data[limb_fk_visuals.IK_SIZE_RECORD_KEY] = json.dumps(sizes)
        else:
            rig.data.pop(limb_fk_visuals.IK_SIZE_RECORD_KEY, None)
    registry = limb_ik._load_source_widget_registry(rig)
    if registry:
        chosen = {n: e for n, e in registry['bones'].items() if ('ARM', e['side']) in keys}
        for name, entry in chosen.items():
            limb_ik._restore_pose_shape_state(rig, rig.pose.bones[name], entry['original'])
        registry['bones'] = {n: e for n, e in registry['bones'].items() if n not in chosen}
        if registry['bones']:
            limb_ik._write_source_widget_registry(rig, registry)
        else:
            rig.data.pop(limb_ik.SOURCE_WIDGETS_KEY, None)


def _prune_widgets(rig, inventory):
    """Delete unreferenced owned leaves; shared widgets survive their users."""
    aid = inventory['armature_id']
    used = {pb.custom_shape for obj in bpy.data.objects if obj.type == 'ARMATURE' for pb in obj.pose.bones if pb.custom_shape}
    remaining = {(b.get(limb_ik.KIND_KEY), b.get(limb_ik.SIDE_KEY)) for b in rig.data.bones
                 if b.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE}
    for obj in tuple(bpy.data.objects):
        if not limb_ik._owned(obj, aid, role='WIDGET') or obj in used:
            continue
        kind = obj.get(limb_ik.KIND_KEY)
        # The Pole arrow can be temporarily hidden by an alternate shape and
        # is still a canonical resource for each surviving Direct limb.
        if _required_widget(kind, remaining):
            continue
        _check_widget_dependencies({obj})
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if not mesh.users:
            bpy.data.meshes.remove(mesh)


def _release_graph(context, rig, inventory, keys, entry):
    selected = {key: inventory['rigs'][key] for key in keys}
    names = {b.name for b in inventory['bones'] if (b.get(limb_ik.KIND_KEY), b.get(limb_ik.SIDE_KEY)) in keys}
    for key, data in selected.items():
        if data.get('foot_controls'):
            foot_controls.remove(context, rig, key)
    inventory = limb_ik._validate_inventory(rig)
    # Foot teardown crosses Edit/Object boundaries.  Never keep RNA references
    # from the earlier inventory across those native topology changes.
    selected = {key: inventory['rigs'][key] for key in keys}
    _prune_displays(rig, keys, names)
    limb_ik_fk.remove_switching(rig, dict(inventory, rigs=selected))
    for key in keys:
        for pb, con, _record in inventory['rigs'][key]['entries']:
            registry = limb_ik._constraint_registry(pb, strict=True)
            registry.pop(con.name, None)
            pb.constraints.remove(con)
            limb_ik._write_constraint_registry(pb, registry)
    root = root_control.get_record(rig)
    if root:
        root['controls'] = [n for n in root['controls'] if n not in names]
        root['control_states'] = {n: e for n, e in root['control_states'].items() if n not in names}
        rig.data[root_control.RECORD_KEY] = json.dumps(root)
    limb_ik._mode_set(context, rig, 'EDIT')
    mirror = rig.data.use_mirror_x
    rig.data.use_mirror_x = False
    try:
        for name in names:
            rig.data.edit_bones.remove(rig.data.edit_bones[name])
        limb_ik._mode_set(context, rig, 'OBJECT')
    finally:
        rig.data.use_mirror_x = mirror
    registry = limb_ik._load_direct_rest_registry(rig)
    for data in selected.values():
        registry['limbs'].pop(data['rig_id'], None)
    limb_ik._write_direct_rest_registry(rig, registry)
    if not registry['limbs']:
        rig.data.pop(limb_ik.TARGET_ROTATION_VERSION_KEY, None)
    _prune_widgets(rig, inventory)
    rig.data.update_tag()
    context.view_layer.update()


def _show_edit_region(rig, key, entry):
    label = ('Left ' if key[1] == 'L' else 'Right ') + ('Leg' if key[0] == 'LEG' else 'Arm')
    collection = rig.data.collections.new('Edit ' + label)
    collection['character_designer_body_region_edit'] = _key(key)
    collection.is_visible = True
    entry['edit_collection'] = collection.name
    entry['display_before'] = {}
    for name in entry['allowed']:
        bone = rig.data.bones[name]
        entry['display_before'][name] = {'hide': bool(bone.hide), 'hide_select': bool(bone.hide_select)}
        collection.assign(bone)
        bone.hide = False
        bone.hide_select = False


def _finish_edit_region(rig, key, entry, inventory):
    collection = rig.data.collections.get(entry.get('edit_collection', ''))
    if collection:
        if (collection.get('character_designer_body_region_edit') != _key(key)
                or any(b.name not in entry['allowed'] for b in collection.bones)):
            raise ValueError('The regional editing collection has artist changes; preserve it first.')
        rig.data.collections.remove(collection)
    for name, display in entry.get('display_before', {}).items():
        bone = rig.data.bones[name]
        bone.hide, bone.hide_select = display['hide'], display['hide_select']
    from . import bone_collections
    body = bone_collections.body_collection(rig)
    if body:
        data = inventory['rigs'][key]
        for name in (data['target'].name, data['pole'].name):
            body.assign(rig.data.bones[name])
        foot = data.get('foot_controls')
        if foot:
            for role in ('FOOT_ROLL', 'TOE_BEND'):
                if name := foot['bones'].get(role):
                    body.assign(rig.data.bones[name])


def _build_selected_rings(context, rig, keys):
    """Use the visual builder's owned leaves with an explicit region filter."""
    import uuid
    from . import generated_names
    inventory = limb_ik._validate_inventory(rig)
    previous = limb_fk_visuals.validate(rig, inventory)
    record = previous or {'version': limb_fk_visuals.VERSION, 'id': uuid.uuid4().hex,
                          'armature_id': inventory['armature_id'], 'bindings': {}, 'skipped': {},
                          'collection': generated_names.collection_name(rig, 'FK Rings')}
    for key in sorted(keys):
        for name in inventory['rigs'][key]['chain'][:2]:
            if name in record['bindings']:
                continue
            pb = rig.pose.bones[name]
            if pb.custom_shape is not None or pb.custom_shape_transform is not None:
                continue
            radius = float(pb.bone.length) * limb_fk_visuals.RADIUS_FACTORS[key[0]]
            widget_name = generated_names.widget_name(rig, 'FK_' + name)
            record['bindings'][name] = {'kind': key[0], 'side': key[1],
                'chain': list(inventory['rigs'][key]['chain']), 'object': widget_name, 'mesh': widget_name,
                'original': limb_ik._pose_shape_json_state(pb),
                'generated': {'custom_shape': '', 'custom_shape_transform': '', 'use_bone_size': False,
                              'scale': [radius] * 3, 'translation': [0., 0., 0.], 'rotation': [0., 0., 0.],
                              'wire_width': 2.0 if hasattr(pb, 'custom_shape_wire_width') else None}}
            limb_fk_visuals._create_widget(context, rig, record, name, record['bindings'][name])
    if record['bindings']:
        rig.data[limb_fk_visuals.RECORD_KEY] = json.dumps(record)


def _build_selected_source_shapes(context, rig, keys):
    inventory = limb_ik._validate_inventory(rig)
    chains = [limb_ik.LimbChain(*key, *inventory['rigs'][key]['chain']) for key in keys if key[0] == 'ARM']
    if chains:
        transaction = limb_ik._new_transaction()
        collection = limb_ik._ensure_widget_collection(context, inventory['armature_id'], transaction, armature=rig)
        limb_ik._decorate_source_widgets(context, rig, inventory['armature_id'], chains, collection,
                                        transaction, schema=inventory['schema'])


def release(context, rig, region, *, _style_checkpoints=None):
    from . import region_styles
    _require(context, rig)
    keys = set(_keys(region))
    if state(rig, region)['status'] != 'BOUND':
        raise ValueError(state(rig, region)['reason'])
    inventory = limb_ik._validate_inventory(rig)
    if inventory['schema'] != limb_ik.DIRECT_PREROLL_SCHEMA:
        raise ValueError('Regional release currently requires current Direct controls; keep this older solver intact.')
    limb_ik._removal_resources(context, rig, inventory)
    entries = _load(rig)
    _validate_receipts(rig, entries)
    owner_proof = _ownership_seed(rig)
    all_rest = body_calibration.native_rest(rig)
    selected = {key: inventory['rigs'][key] for key in keys}
    names = {b.name for b in inventory['bones'] if (b.get(limb_ik.KIND_KEY), b.get(limb_ik.SIDE_KEY)) in keys}
    cons = {(pb.name, con.name) for d in selected.values() for pb, con, _r in d['entries']}
    drivers = {con.path_from_id('influence') for d in selected.values() for pb, con, record in d['entries']
               if limb_ik_fk.is_switch_constraint(rig, pb, con, record)}
    for key, data in selected.items():
        foot = data.get('foot_controls')
        if foot:
            names.update(foot['bones'].values())
            cons.update((e['owner'], e['name']) for e in foot['constraints'])
            cons.update((e['owner'], e['name']) for e in foot['original_constraints'])
            drivers.update(e['path'] for e in foot['drivers'])
    allowed = set().union(*(_native_scope(rig, d['chain']) for d in selected.values()))
    affected = set(names) | allowed
    _animations(rig, affected, drivers)
    _dependencies(rig, names, cons, drivers)
    _check_widget_dependencies(_widget_candidates(rig, inventory, keys, names))
    display_names = {n for n, e in (inventory['source_widgets'] or {}).get('bones', {}).items()
                     if ('ARM', e['side']) in keys}
    _style_dependency_preflight(rig, inventory, keys, affected | display_names)
    calibration = rig.get(body_calibration.KEY)
    from . import forearm_twist
    correctives = {o.name: o.get(forearm_twist.RECORD_KEY) for o in bpy.data.objects if forearm_twist.RECORD_KEY in o}
    rings = limb_fk_visuals.get_record(rig)
    for key, data in selected.items():
        scope = _native_scope(rig, data['chain'])
        foot = data.get('foot_controls')
        entries[_key(key)] = {'key': _key(key), 'schema': inventory['schema'], 'armature_id': inventory['armature_id'],
            'chain': list(data['chain']),
            'allowed': sorted(scope), 'relations': {n: all_rest[n] for n in scope},
            'protected_rest': {n: e for n, e in all_rest.items() if n not in scope},
            'feature': inventory['target_rotation_version'], 'auto_align': bool(data['auto_align']),
            'foot': {'toe': foot['toe']} if foot else None,
            'rings': bool(rings and any((e['kind'], e['side']) == key for e in rings['bindings'].values())),
            'source_shapes': bool(inventory['source_widgets'] and any(('ARM', e['side']) == key for e in inventory['source_widgets']['bones'].values())),
            'source_names': sorted(n for n in display_names if (inventory['source_widgets']['bones'][n]['side'] == key[1])),
            'pole_distance': float(limb_ik._settings(context).pole_distance_ratio),
            'pole_layout': _capture_pole_layout(rig, key, data)}

    captured_styles = []

    def commit():
        protected = _guard(rig, names | allowed | display_names)
        skin = body_setup._native_skin(rig)
        surfaces = body_setup_removal._bound_surfaces(context, rig)
        for key in sorted(keys):
            styles = region_styles.capture(context, rig, {key}, inventory)
            captured_styles.append(styles)
            if _style_checkpoints is not None:
                _style_checkpoints.append(styles)
            entries[_key(key)]['styles'] = styles
        _release_graph(context, rig, inventory, keys, entries)
        body_setup.preserve_object_inputs(context, rig)
        body_setup_removal._preserve_skin(context, rig, {n: skin[n] for n in allowed}, prefer_existing_basis=True)
        body_calibration.verify_rest(rig, all_rest)
        body_setup._verify_skin(rig, skin, tolerance=body_setup_removal.SKIN_MATRIX_TOLERANCE)
        error = body_setup_removal._check_surfaces(context, rig, surfaces)
        limb_ik._validate_inventory(rig)
        _verify_guard(rig, protected, names | allowed | display_names)
        if rig.get(body_calibration.KEY) != calibration:
            raise ValueError('Regional release changed calibration; the operation was rolled back.')
        if any(bpy.data.objects[n].get(forearm_twist.RECORD_KEY) != raw for n, raw in correctives.items()):
            raise ValueError('Regional release changed a Forearm correction profile.')
        for key in sorted(keys):
            _show_edit_region(rig, key, entries[_key(key)])
        _save(rig, entries)
        _finish_owner_revision(rig, owner_proof)
        return {'released': sorted(_key(k) for k in keys), 'bones_removed': len(names), 'surface_error': error}
    try:
        with _ownership_operation(rig, owner_proof):
            return body_setup._atomic(context, rig, commit)
    except Exception as error:
        # A same-rig rebuild joins the outer checkpoint. Its owner must decide
        # cleanup versus recovery retention AFTER that outer rollback finishes.
        # Keep complete style packets even when release never saves its receipt.
        if _style_checkpoints is not None:
            raise
        for styles in captured_styles:
            if 'In-place recovery also failed:' in str(error):
                region_styles.retain_for_recovery(rig, styles)
            else:
                region_styles.cleanup(rig, styles)
        raise


@contextmanager
def _admission(rig, entries, keys):
    global _BIND_AUTH
    if _BIND_AUTH is not None:
        raise ValueError('Another regional rebind is in progress.')
    _BIND_AUTH = {'rig': rig, 'data': rig.data,
                  'entries': entries,
                  'chains': {tuple(entries[_key(k)]['chain']): k for k in keys}}
    try:
        yield
    finally:
        _BIND_AUTH = None


def authorize_build(rig, plans):
    auth = _BIND_AUTH
    if (auth is None or auth['rig'] is not rig or auth['data'] is not rig.data
            or {tuple(p.chain.names): (p.chain.kind, p.chain.side) for p in plans} != auth['chains']):
        raise ValueError('Regional rebuild requires the exact active saved-release admission.')


def attach_new_root_inputs(context, rig, plans):
    authorize_build(rig, plans)
    record = root_control.get_record(rig)
    if record is None:
        return
    names = [n for p in plans for n in (p.target_name, p.pole_name)]
    for name in names:
        bone = rig.data.bones[name]
        if bone.parent:
            raise ValueError('A new regional input already has an unexpected parent.')
        record['control_states'][name] = root_control._state(bone)
    record['controls'] = sorted(set(record['controls']) | set(names))
    mirror = rig.data.use_mirror_x
    rig.data.use_mirror_x = False
    try:
        limb_ik._mode_set(context, rig, 'EDIT')
        for name in names:
            rig.data.edit_bones[name].parent = rig.data.edit_bones[record['master']]
        limb_ik._mode_set(context, rig, 'OBJECT')
    finally:
        rig.data.use_mirror_x = mirror
    rig.data[root_control.RECORD_KEY] = json.dumps(record)
    rig_ids = {p.rig_id for p in plans}
    for owner, constraint, owned in limb_ik._owned_constraint_records(rig):
        if owned.get('rig_id') in rig_ids and owned.get('rotation_space') == 'PARENT_DELTA':
            limb_ik._configure_parent_delta_rotation(rig, rig.pose.bones[owned['target']], constraint)
    rig.update_tag(refresh={'OBJECT'})
    context.view_layer.update()


def bind(context, rig, region, *, _defer_style_cleanup=False):
    from . import region_styles
    _require(context, rig)
    keys = set(_keys(region))
    if state(rig, region)['status'] != 'RELEASED':
        raise ValueError(state(rig, region)['reason'])
    entries = _load(rig)
    current_rest = body_calibration.native_rest(rig)
    all_allowed = _validate_receipts(rig, entries)
    owner_proof = _ownership_seed(rig)
    saved_styles = [entries[_key(key)]['styles'] for key in sorted(keys)
                    if 'styles' in entries[_key(key)]]
    for key in keys:
        styles = entries[_key(key)].get('styles')
        if styles is not None and (not isinstance(styles, dict) or styles.get('keys') != [_key(key)]):
            raise ValueError('A regional display checkpoint belongs to another selected part.')
    for styles in saved_styles:
        region_styles.validate(rig, styles)
    for key in keys:
        entry = entries[_key(key)]
        if set(current_rest) != set(entry['protected_rest']) | set(entry['allowed']):
            raise ValueError('Native bones were added or removed; regional rebuild only accepts Rest geometry edits.')
        if any(n not in current_rest for n in entry['allowed']):
            raise ValueError('A released native bone was deleted or renamed; restore it before regional rebuild.')
        body_calibration.verify_rest(rig, entry['protected_rest'], allowed=all_allowed)
        for name in entry['allowed']:
            # Relationships are not a regional proportion edit.
            before = entry.get('relations', {}).get(name)
            if before and any(current_rest[name][k] != before[k] for k in ('parent', 'use_connect', 'use_deform')):
                raise ValueError('Native relationships changed; regional rebuild only accepts Rest geometry edits.')
    selected_allowed = {n for key in keys for n in entries[_key(key)]['allowed']}
    display_names = {n for key in keys for n in entries[_key(key)].get('source_names', ())}
    _animations(rig, selected_allowed)
    from . import forearm_twist
    for obj in bpy.data.objects:
        if forearm_twist.RECORD_KEY not in obj:
            continue
        for correction in forearm_twist._records(obj).values():
            if (correction.get('armature') == rig.name and set(correction.get('chain', ())) & selected_allowed
                    and correction.get('rest') != forearm_twist._rest_signature(rig, correction['chain'])):
                raise ValueError('This edited arm has an older Forearm correction profile. Remove that old correction, rebuild this arm, then capture its correction again.')
    calibration = rig.get(body_calibration.KEY)

    def commit():
        protected = _guard(rig, selected_allowed | display_names)
        skin = body_setup._native_skin(rig)
        surfaces = body_setup_removal._bound_surfaces(context, rig)
        plans = []
        for key in sorted(keys):
            entry = entries[_key(key)]
            chain = limb_ik.LimbChain(*key, *entry['chain'])
            _s, _e, _w, direction = body_calibration.direction(rig, chain)
            if direction is None:
                raise ValueError('Give the edited knee/elbow a small stable Rest bend before rebuilding.')
            plan = limb_ik._build_plan(rig, chain, entry['pole_distance'], direction)
            plan.auto_align = entry['auto_align']
            plans.append(plan)
        with _admission(rig, entries, keys):
            limb_ik._build_plans(context, rig, plans, schema=limb_ik.DIRECT_PREROLL_SCHEMA,
                                decorate_source_widgets=False,
                                target_rotation_version=entries[_key(next(iter(keys)))]['feature'],
                                region_rebind=True)
        body_setup.preserve_object_inputs(context, rig)
        for key in sorted(keys):
            entry = entries[_key(key)]
            if entry['foot']:
                from . import character_setup
                foot_controls.build(context, rig, key, toe_name=entry['foot']['toe'],
                                    shoe=character_setup.footwear_reference(context, rig))
        if any(entries[_key(k)]['rings'] for k in keys):
            _build_selected_rings(context, rig, {k for k in keys if entries[_key(k)]['rings']})
        if any(entries[_key(k)]['source_shapes'] for k in keys):
            _build_selected_source_shapes(context, rig, {k for k in keys if entries[_key(k)]['source_shapes']})
        inventory = limb_ik._validate_inventory(rig)
        style_bones = {name for styles in saved_styles for name in styles['bones']}
        for styles in saved_styles:
            region_styles.restore(context, rig, styles, inventory, allowed_bones=style_bones)
        inventory = limb_ik._validate_inventory(rig)
        limb_ik._removal_resources(context, rig, inventory)
        body_calibration.verify_rest(rig, current_rest)
        body_setup._verify_skin(rig, skin, tolerance=body_setup_removal.SKIN_MATRIX_TOLERANCE)
        body_setup.preserve_object_inputs(context, rig)
        error = body_setup_removal._check_surfaces(context, rig, surfaces)
        new_names = {b.name for b in inventory['bones'] if (b.get(limb_ik.KIND_KEY), b.get(limb_ik.SIDE_KEY)) in keys}
        for key in keys:
            foot = inventory['rigs'][key].get('foot_controls')
            if foot:
                new_names.update(foot['bones'].values())
        _verify_guard(rig, protected, selected_allowed | new_names | display_names)
        if rig.get(body_calibration.KEY) != calibration:
            raise ValueError('Regional rebuild changed calibration; the operation was rolled back.')
        for key in keys:
            _finish_edit_region(rig, key, entries[_key(key)], inventory)
            del entries[_key(key)]
        # Other intentionally released regions may still be under editing.
        # Newly accepted Rest becomes their protected reference, not an old
        # version of the just-rebuilt opposite region.
        for entry in entries.values():
            entry['protected_rest'] = {n: e for n, e in current_rest.items() if n not in entry['allowed']}
        _save(rig, entries)
        _finish_owner_revision(rig, owner_proof)
        return {'bound': sorted(_key(k) for k in keys), 'surface_error': error}
    with _ownership_operation(rig, owner_proof):
        result = body_setup._atomic(context, rig, commit)
    if not _defer_style_cleanup:
        for styles in saved_styles:
            region_styles.cleanup(rig, styles)
    return result


def rebuild(context, rig, region):
    """Rebuild one bound part in a single undoable operation, keeping its styles."""
    from . import region_styles
    _require(context, rig)
    if state(rig, region)['status'] != 'BOUND':
        raise ValueError('Choose a part whose controls are ready before rebuilding it.')
    selected = set(_keys(region))
    inventory = limb_ik._validate_inventory(rig)
    affected = {name for key in selected for name in _native_scope(rig, inventory['rigs'][key]['chain'])}
    affected.update(b.name for b in inventory['bones']
                    if (b.get(limb_ik.KIND_KEY), b.get(limb_ik.SIDE_KEY)) in selected)
    for key in selected:
        foot = inventory['rigs'][key].get('foot_controls')
        if foot:
            affected.update(foot['bones'].values())
    _style_dependency_preflight(rig, inventory, selected, affected)
    rest = body_calibration.native_rest(rig)
    styles_created = []
    previous_style_tokens = region_styles.backup_tokens(rig)

    def commit():
        release(context, rig, region, _style_checkpoints=styles_created)
        result = bind(context, rig, region, _defer_style_cleanup=True)
        body_calibration.verify_rest(rig, rest)
        return dict(result, rebuilt=result['bound'], custom_styles_preserved=True)

    try:
        result = body_setup._atomic(context, rig, commit)
    except Exception as error:
        styles_created.extend(region_styles.created_backups(rig, previous_style_tokens))
        for styles in styles_created:
            if 'In-place recovery also failed:' in str(error):
                region_styles.retain_for_recovery(rig, styles)
            else:
                region_styles.cleanup(rig, styles)
        raise
    else:
        styles_created.extend(region_styles.created_backups(rig, previous_style_tokens))
        for styles in styles_created:
            region_styles.cleanup(rig, styles)
        return result


def _style_dependency_preflight(rig, inventory, selected, affected):
    # Keep the same external-reference protection as intentional Rest resync,
    # including Hook/Warp bones and authored readers of matched input channels.
    from . import body_rest_resync
    body_rest_resync._refuse_dependencies(rig, inventory, selected)
    own_constraints = body_rest_resync._owned_constraint_pointers(rig, inventory)
    for obj in bpy.data.objects:
        owners = [obj]
        if obj.type == 'ARMATURE' and obj.pose:
            owners.extend(obj.pose.bones)
        for owner in owners:
            for con in owner.constraints:
                if (obj.as_pointer(), owner.as_pointer(), con.as_pointer()) in own_constraints:
                    continue
                if (getattr(con, 'space_object', None) is rig
                        and getattr(con, 'space_subtarget', '') in affected):
                    raise ValueError('An external constraint uses this part as its custom space; keep that dependency intact.')
