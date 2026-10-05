"""Accept intentional Direct source Rest edits without re-planing author bones.

This is a bounded Original -> Controls transaction, not a general rig repair.
The caller owns pose/display/Dress rollback and defers corrective runtime until
the final graph. Only accepted Direct chain provenance is written here; native
Rest, model geometry and existing pose-asset compatibility baselines stay intact.
"""
import json
import math
from array import array
from datetime import datetime, timezone

import bpy
from mathutils import Matrix, Vector

from . import limb_ik, limb_ik_fk, control_pose_assets as poses

PROVENANCE_KEY = 'character_designer_body_rest_resync_v1'
_RELATIONS = ('parent', 'connected', 'inherit_scale', 'inherit_rotation', 'local_location')


def _clone(value):
    return json.loads(json.dumps(value))


def _geometry_changes(rig, saved):
    """Validate both records completely; accept geometry, never structural edits."""
    from . import body_original_mode as original
    old = saved.get('rest') if isinstance(saved, dict) else None
    # Reuse the established schema/finite checks, with no baseline mutation.
    original._validate_session_rest(rig, saved, current=old)
    current = poses.native_rest(rig)
    if set(current) != set(old):
        raise ValueError('Original native ownership changed; keep Original and inspect the skeleton.')
    original._validate_session_rest(rig, {**saved, 'rest': current}, current=current)
    changed = set()
    for name, before in old.items():
        now = current[name]
        relations = [key for key in _RELATIONS if before[key] != now[key]]
        if relations:
            raise ValueError(f'Original structure changed at {name} ({", ".join(relations)}); Rest resync cannot accept that change.')
        error = max(abs(a-b) for first, second in zip(before['matrix'], now['matrix'])
                    for a, b in zip(first, second))
        if (abs(before['length'] - now['length']) > original._REST_LENGTH_EPSILON
                or error > original._REST_MATRIX_EPSILON):
            changed.add(name)
    return current, changed


class OriginalRestView:
    """Exact prior Rest proof for one already validated Original transaction.

    Only Direct applied-Rest comparisons consume this view. It never substitutes
    live RNA or changes how ordinary inventory checks read a bone.
    """
    def __init__(self, rig, saved, current, changed):
        from . import body_original_mode as original
        self.pointer = rig.as_pointer()
        self.session_raw = rig[original.SESSION]
        self.saved_rest = _clone(saved['rest'])
        self.current_rest = _clone(current)
        self.changed = frozenset(changed)

    def validate(self, rig, saved):
        from . import body_original_mode as original
        if (rig.as_pointer() != self.pointer or rig.get(original.SESSION) != self.session_raw
                or saved.get('rest') != self.saved_rest):
            raise ValueError('The Original Rest resync plan no longer belongs to this session.')
        current, changed = _geometry_changes(rig, saved)
        if current != self.current_rest or changed != set(self.changed):
            raise ValueError('The native Rest changed after resync preflight; inspect the current edits again.')

    def _prior(self, bone):
        entry = self.saved_rest[bone.name]
        matrix = Matrix(entry['matrix'])
        head = matrix.translation
        tail = head + matrix.to_3x3().col[1] * entry['length']
        return head, tail, matrix.to_3x3().col[2], entry

    def matches(self, bone, state):
        if bone is None or bone.name not in self.changed:
            return limb_ik._rest_state_matches(bone, state)
        head, tail, z_axis, entry = self._prior(bone)
        expected_z = Vector(state['z'])
        tolerance = max(float(entry['length']), 1.0) * 1e-6
        return (min(z_axis.length, expected_z.length) > limb_ik.EPSILON
                and (head - Vector(state['head'])).length <= tolerance
                and (tail - Vector(state['tail'])).length <= tolerance
                and z_axis.normalized().dot(expected_z.normalized()) >= 1.0 - 1e-6
                and (entry['parent'] or '') == state['parent']
                and entry['connected'] == state['use_connect'])

    def details(self, bone, state):
        if bone is None or bone.name not in self.changed:
            return limb_ik._rest_state_mismatch_details(bone, state)
        head, tail, z_axis, _entry = self._prior(bone)
        return (f"saved Original head={(head - Vector(state['head'])).length:.3g}, "
                f"tail={(tail - Vector(state['tail'])).length:.3g}, "
                f"z={1.0 - z_axis.normalized().dot(Vector(state['z']).normalized()):.3g}")


def _provenance(rig):
    raw = rig.get(PROVENANCE_KEY)
    if raw is None:
        return {'version': 1, 'events': []}
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError('The previous Rest resync provenance is invalid; preserve its saved file.') from exc
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 1 or not isinstance(value.get('events'), list)
            or any(not isinstance(entry, dict) for entry in value['events'])):
        raise ValueError('The previous Rest resync provenance schema is unsupported; preserve its saved file.')
    return value


def _owned_constraint_pointers(rig, inventory):
    from . import torso_controls, spine_ik_fk, eye_controls, root_control, foot_controls
    constraints = {(pb.as_pointer(), con.as_pointer())
                   for pb, con, _record in inventory['records']}
    records = [module.get_record(rig) for module in
               (torso_controls, spine_ik_fk, eye_controls, root_control)]
    records.extend(foot_controls.records(rig).values())
    for record in records:
        for entry in record['constraints'] if record else ():
            pb = rig.pose.bones[entry['owner']]
            constraints.add((pb.as_pointer(), pb.constraints[entry['name']].as_pointer()))
    return {(rig.as_pointer(), pb, con) for pb, con in constraints}


def _refuse_dependencies(rig, inventory, keys):
    from . import foot_controls
    chains = [inventory['rigs'][key]['chain'] for key in keys]
    own_constraints = _owned_constraint_pointers(rig, inventory)
    problems = limb_ik._direct_source_dependency_problems(
        rig, chains, owned_constraints=own_constraints)
    if problems:
        raise ValueError('Rest resync cannot preserve this dependency: ' + problems[0] + '.')
    affected = {name for chain in chains for name in chain}
    for key in keys:
        entry = inventory['rigs'][key]
        affected.update(bone.name for bone in (entry['target'], entry['pole']) if bone is not None)
        affected.update(pb.name for pb, _con, _record in entry['entries'])
    # A Rest edit of an ancestor also reinterprets descendant local channels.
    affected.update(b.name for b in rig.data.bones
                    if any(parent.name in affected for parent in b.parent_recursive))
    owned_paths = limb_ik_fk.owned_driver_paths(rig)
    for owner in (rig, rig.data):
        for action in limb_ik._actions_for_id(owner):
            if any(limb_ik._path_mentions_bone(curve.data_path, affected)
                   for curve in limb_ik._fcurves_for_action(action)):
                raise ValueError(f"Action '{action.name}' animates the Rest resync region; preserve or retarget that animation first.")
        for curve in owner.animation_data.drivers if owner.animation_data else ():
            if owner == rig and curve.data_path in owned_paths:
                continue
            if limb_ik._path_mentions_bone(curve.data_path, affected):
                raise ValueError('An authored driver writes to the Rest resync region; preserve that dependency first.')
    # Matching changes authored local channels even when the final skin is
    # identical. Do not reinterpret external readers of those local values.
    for owner in foot_controls._driver_owners():
        animation = getattr(owner, 'animation_data', None)
        for curve in animation.drivers if animation else ():
            if owner == rig and curve.data_path in owned_paths:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id == rig and (target.bone_target in affected
                            or limb_ik._path_mentions_bone(target.data_path, affected)):
                        raise ValueError('An external driver reads the Rest resync region; preserve that dependency first.')
    for obj in bpy.data.objects:
        if any(limb_ik._constraint_references_controls(con, rig, affected) for con in obj.constraints):
            raise ValueError(f"An object constraint on '{obj.name}' reads the Rest resync region; preserve that dependency first.")
        if obj.type != 'ARMATURE' or obj.pose is None:
            continue
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if (obj.as_pointer(), pb.as_pointer(), con.as_pointer()) in own_constraints:
                    continue
                if limb_ik._constraint_references_controls(con, rig, affected):
                    raise ValueError(f"An authored constraint on '{obj.name}:{pb.name}' reads the Rest resync region; preserve that dependency first.")


def _current_direct_state(rig, name):
    bone = rig.data.bones[name]
    head, tail = Vector(bone.head_local), Vector(bone.tail_local)
    axis = tail - head
    z_axis = limb_ik._project_perpendicular(bone.matrix_local.to_3x3().col[2], axis)
    if min(axis.length, z_axis.length) <= limb_ik.EPSILON:
        raise ValueError(f'Native Rest bone {name} has no finite usable axis.')
    z_axis.normalize()
    state = {'head': list(head), 'tail': list(tail), 'z': list(z_axis),
             'roll': limb_ik._roll_for_axes(axis, z_axis),
             'parent': bone.parent.name if bone.parent else '',
             'use_connect': bool(bone.use_connect)}
    return limb_ik._direct_rest_state(state, 'Accepted author Rest ' + name)


def _corrective_checkpoint(rig):
    """Capture only owned outputs on meshes bound to this character."""
    from . import forearm_twist
    result = []
    for obj in bpy.data.objects:
        if (obj.type != 'MESH' or forearm_twist.RECORD_KEY not in obj
                or not any(mod.type == 'ARMATURE' and mod.object == rig for mod in obj.modifiers)):
            continue
        records = forearm_twist._records(obj)
        keys = obj.data.shape_keys
        outputs, seen = [], set()
        for side, record in records.items():
            key = forearm_twist._managed_key(obj, side, record, required=False, repair_name=False)
            if key is None or key.as_pointer() in seen:
                continue
            seen.add(key.as_pointer())
            coords = array('f', [0.0]) * (len(key.data) * 3)
            key.data.foreach_get('co', coords)
            outputs.append({'key': key, 'pointer': key.as_pointer(), 'name': key.name,
                            'coords': coords, 'value': key.value, 'mute': key.mute})
        pointer = obj.as_pointer()
        result.append({'object': obj, 'pointer': pointer, 'mesh': obj.data,
                       'keys': keys, 'key_pointers': {key.as_pointer() for key in keys.key_blocks} if keys else set(),
                       'record': obj.get(forearm_twist.RECORD_KEY), 'outputs': outputs,
                       'active_index': obj.active_shape_key_index,
                       'references': {identity: key for identity, key in forearm_twist._KEY_REFERENCES.items()
                                      if identity[0] == pointer},
                       'error_present': obj.name in forearm_twist._ERRORS,
                       'error': forearm_twist._ERRORS.get(obj.name)})
    return result


def restore_correctives(plan):
    """Restore after the caller's final rollback flush, while runtime is paused.

    The runtime can mirror a recorded side into a new owned output. Such a key
    is removed only with both the runtime identity and current record proving
    ownership; every preexisting KeyBlock is retained regardless of its name.
    """
    if plan is None:
        return
    from . import forearm_twist
    previous_busy = forearm_twist._BUSY
    forearm_twist._BUSY = True
    try:
        for state in plan.get('correctives', ()):
            obj, pointer = state['object'], state['pointer']
            if obj.as_pointer() != pointer or obj.data != state['mesh'] or obj.data.shape_keys != state['keys']:
                raise ValueError('A corrective datablock was replaced during Rest resync; preserve the recovery file.')
            keys = obj.data.shape_keys
            if keys:
                current_pointers = {key.as_pointer() for key in keys.key_blocks}
                if not state['key_pointers'].issubset(current_pointers):
                    raise ValueError('A preexisting Shape Key disappeared during Rest resync; preserve the recovery file.')
                records = forearm_twist._records(obj)
                added_owned = set()
                for side, record in records.items():
                    key = forearm_twist._KEY_REFERENCES.get((pointer, side))
                    if (key is not None and key.id_data == keys and key.name == record.get('key')
                            and key.as_pointer() not in state['key_pointers']):
                        added_owned.add(key.as_pointer())
                additions = current_pointers - state['key_pointers']
                if additions - added_owned:
                    raise ValueError('An unowned Shape Key appeared during Rest resync; do not remove artist data.')
                for key in tuple(keys.key_blocks):
                    if key.as_pointer() in additions:
                        obj.shape_key_remove(key)
                for output in state['outputs']:
                    key = output['key']
                    if key.as_pointer() != output['pointer'] or len(key.data) * 3 != len(output['coords']):
                        raise ValueError('A managed corrective output changed identity during Rest resync.')
                    key.name = output['name']
                    key.data.foreach_set('co', output['coords'])
                    key.value, key.mute = output['value'], output['mute']
            obj[forearm_twist.RECORD_KEY] = state['record']
            obj.active_shape_key_index = state['active_index']
            for identity in tuple(forearm_twist._KEY_REFERENCES):
                if identity[0] == pointer:
                    forearm_twist._KEY_REFERENCES.pop(identity, None)
            forearm_twist._KEY_REFERENCES.update(state['references'])
            forearm_twist._CACHE.pop(pointer, None)
            forearm_twist._OUTPUT_CACHE.pop(pointer, None)
            if state['error_present']:
                forearm_twist._ERRORS[obj.name] = state['error']
            else:
                forearm_twist._ERRORS.pop(obj.name, None)
            if keys:
                keys.update_tag()
            obj.data.update()
        forearm_twist.validation_cache.clear()
    finally:
        forearm_twist._BUSY = previous_busy


def prepare(context, rig, saved):
    """Read-only bounded Direct resync plan, or None for an unchanged Rest."""
    from . import body_original_mode as original, forearm_original_inventory
    from . import body_setup_removal
    if (rig is None or rig.type != 'ARMATURE' or rig.library or rig.data.library
            or rig.override_library or rig.data.users != 1 or context.mode not in {'OBJECT', 'POSE'}):
        raise ValueError('Rest resync needs a local single-user rig in Object or Pose Mode.')
    current, changed = _geometry_changes(rig, saved)
    if not changed:
        return None
    schema = rig.data.get(limb_ik.SCHEMA_KEY, limb_ik.LEGACY_SCHEMA)
    if not limb_ik._is_direct_preroll_schema(schema):
        raise ValueError('Intentional Rest resync currently supports Direct controls only; keep Original and preserve the current skeleton.')
    registry = limb_ik._load_direct_rest_registry(rig, strict=True)
    registry_raw = rig.data.get(limb_ik.DIRECT_REST_KEY)
    raw_entries = json.loads(registry_raw)['limbs']
    supported = {name for entry in registry['limbs'].values() for name in entry['chain'][:2]}
    unsupported = changed - supported
    if unsupported:
        raise ValueError('Rest resync supports existing Direct upper/lower source frames only: '
                         + ', '.join(sorted(unsupported)[:4]) + '. Keep Original and preserve these edits.')
    view = OriginalRestView(rig, saved, current, changed)
    inventory = forearm_original_inventory.validate(rig, original_rest=view)
    keys = {key for key, entry in inventory['rigs'].items()
            if changed.intersection(entry['chain'][:2])}
    if not keys:
        raise ValueError('No verified Direct chain owns the edited native Rest.')
    _refuse_dependencies(rig, inventory, keys)
    new_registry = _clone(registry)
    prior_entries, accepted_entries = {}, {}
    for key in sorted(keys):
        entry = inventory['rigs'][key]
        rig_id = entry['rig_id']
        prior_entries[rig_id] = _clone(raw_entries[rig_id])
        states = {name: _current_direct_state(rig, name) for name in entry['chain'][:2]}
        upper, lower = (states[name] for name in entry['chain'][:2])
        tolerance = max((Vector(upper['tail']) - Vector(upper['head'])).length,
                        (Vector(lower['tail']) - Vector(lower['head'])).length, 1.0) * 1e-6
        if ((Vector(upper['tail']) - Vector(lower['head'])).length > tolerance
                or lower['parent'] != entry['chain'][0]):
            raise ValueError(f'{key[1]} {key[0].title()} Rest chain is geometrically disconnected; preserve and inspect that edit before resync.')
        # This author Rest is the new removal endpoint. Retain the exact older
        # pre-roll history separately, rather than letting Remove undo this edit.
        accepted = {**new_registry['limbs'][rig_id], 'original': _clone(states), 'applied': _clone(states)}
        new_registry['limbs'][rig_id] = accepted
        accepted_entries[rig_id] = _clone(accepted)
    context.view_layer.update()
    native = poses.native_rest(rig)
    desired = original._pose(rig, native)
    skin = {name: matrix @ rig.data.bones[name].matrix_local.inverted()
            for name, matrix in desired.items()}
    channels = original._channels(rig)
    return {'rig': rig, 'view': view, 'rest': native, 'changed': changed, 'keys': keys,
            'sources': {name for key in keys for name in inventory['rigs'][key]['chain']},
            'desired': desired, 'skin': skin,
            'surfaces': body_setup_removal._bound_surfaces(context, rig),
            'deform_flags': {b.name: bool(b.use_deform) for b in rig.data.bones},
            'registry_raw': registry_raw, 'registry': new_registry,
            'session_raw': rig[original.SESSION], 'provenance_raw': rig.get(PROVENANCE_KEY),
            'provenance': _provenance(rig), 'prior_entries': prior_entries,
            'accepted_entries': accepted_entries, 'channels': channels,
            'correctives': _corrective_checkpoint(rig),
            'pole_angles': [(con, float(con.pole_angle)) for _pb, con, record in inventory['records']
                            if record['role'] == 'IK'], 'applied': False, 'verified': False}


def apply(context, rig, saved, plan):
    """Install provenance after the caller restored owned mutes, before matching.

    The caller must force pose matching for ``sources`` even when its saved Pose
    channels were unchanged: geometry-only Rest edits still alter evaluation.
    Existing Direct IK/FK matching compensates source roll via the Pole control.
    """
    if plan is None:
        return set()
    if plan['rig'] != rig or plan['applied']:
        raise ValueError('The Rest resync plan was already applied or belongs to another rig.')
    plan['view'].validate(rig, saved)
    if rig.data.get(limb_ik.DIRECT_REST_KEY) != plan['registry_raw']:
        raise ValueError('Direct provenance changed after Rest resync preflight.')
    limb_ik._write_direct_rest_registry(rig, plan['registry'])
    plan['applied'] = True
    try:
        limb_ik._validate_inventory(rig)
    except Exception:
        rig.data[limb_ik.DIRECT_REST_KEY] = plan['registry_raw']
        plan['applied'] = False
        raise
    return set(plan['sources'])


def verify(context, rig, plan):
    """Prove author Rest, full native deformation and all bound surfaces survive."""
    if plan is None:
        return
    from . import body_original_mode as original, body_setup_removal
    if plan['rig'] != rig or not plan['applied']:
        raise ValueError('Rest resync cannot be validated before its graph is restored.')
    context.view_layer.update()
    if poses.native_rest(rig) != plan['rest']:
        raise ValueError('Rest resync changed author bone geometry; the switch must be rolled back.')
    if {b.name: bool(b.use_deform) for b in rig.data.bones} != plan['deform_flags']:
        raise ValueError('Rest resync changed native deformation ownership; the switch must be rolled back.')
    original._verify(rig, plan['desired'])
    limb_ik._validate_inventory(rig)
    for name, before in plan['skin'].items():
        now = rig.pose.bones[name].matrix @ rig.data.bones[name].matrix_local.inverted()
        error = max(abs(a-b) for first, second in zip(before, now) for a, b in zip(first, second))
        if not math.isfinite(error) or error > body_setup_removal.SKIN_MATRIX_TOLERANCE:
            raise ValueError(f'Rest resync could not preserve {name} deformation ({error:.6g}); the switch must be rolled back.')
    body_setup_removal._check_surfaces(context, rig, plan['surfaces'])
    plan['verified'] = True


def commit(rig, plan):
    """Record one accepted transaction after full caller and surface validation."""
    if plan is None:
        return
    if plan['rig'] != rig or not plan['verified']:
        raise ValueError('Rest resync provenance requires successful final validation.')
    if rig.get(PROVENANCE_KEY) != plan['provenance_raw']:
        raise ValueError('Rest resync history changed during the operation; preserve both versions.')
    provenance = _clone(plan['provenance'])
    provenance['events'].append({
        'accepted_utc': datetime.now(timezone.utc).isoformat(),
        'schema': rig.data.get(limb_ik.SCHEMA_KEY),
        'changed': sorted(plan['changed']),
        'prior_direct_registry_raw': plan['registry_raw'],
        'prior_direct_entries': plan['prior_entries'],
        'accepted_direct_entries': plan['accepted_entries'],
        'prior_native_rest': {name: plan['view'].saved_rest[name] for name in plan['changed']},
        'accepted_native_rest': {name: plan['rest'][name] for name in plan['changed']},
        'pose_assets_retargeted': False, 'body_calibration_reconfirmed': False})
    rig[PROVENANCE_KEY] = json.dumps(provenance, separators=(',', ':'))


def rollback(context, rig, plan):
    """Restore exact provenance/session/channels; native Rest was never written."""
    if plan is None:
        return
    from . import body_original_mode as original
    if plan['rig'] != rig:
        raise ValueError('Cannot restore a Rest resync checkpoint on another rig.')
    rig.data[limb_ik.DIRECT_REST_KEY] = plan['registry_raw']
    if plan['provenance_raw'] is None:
        rig.pop(PROVENANCE_KEY, None)
    else:
        rig[PROVENANCE_KEY] = plan['provenance_raw']
    rig[original.SESSION] = plan['session_raw']
    original._restore_channels(rig, plan['channels'])
    for constraint, angle in plan['pole_angles']:
        if constraint.pole_angle != angle:
            constraint.pole_angle = angle
    plan['applied'], plan['verified'] = False, False
    context.view_layer.update()
    restore_correctives(plan)
