"""Explicit current-controls-only acceptance of edited same-ID adopted legs.

This transaction never restores or rewrites an adopted session or an Action.
Historical animation Rest proofs remain authoritative for those old motions.
Only the current control graph and regional tools receive a separate revision.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import hashlib
import json
import struct

import bpy
from mathutils import Vector

from . import body_rest_adoption_policy as policy

MODE = 'CURRENT_CONTROLS_ONLY'
AUDIT_KEY = 'character_designer_current_leg_rest_acceptance_v1'
_ACTIVE = None
_COMPANION_ROLES = ('AUTO_TOE_REF', 'FK_TOE_REF')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _hash(value):
    from . import body_region
    return body_region._hash(value)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def has_revision(rig):
    """Cheap mode dispatch only; a tag alone never grants admission."""
    from . import body_region
    raw = rig.data.get(body_region.RECOVERY_OWNER_KEY)
    if raw is None:
        return False
    value = json.loads(raw)
    _require(isinstance(value, dict), 'The saved regional owner revision is invalid.')
    return value.get('mode') == MODE


def _actual_binding(rig):
    _require(rig is not None and rig.type == 'ARMATURE'
             and not rig.library and not rig.override_library and rig.is_editable
             and not rig.data.library and not rig.data.override_library and rig.data.is_editable,
             'Accept leg Rest needs the original local editable armature.')
    bound = [obj for obj in bpy.data.objects
             if obj.type == 'ARMATURE' and obj.data is rig.data]
    _require(len(bound) == 1 and bound[0] is rig,
             'Another Object actually shares this Armature data; preserve both bindings.')


def _historical_pair(rig, previous, motion_rest, mapped_names, motion=None):
    """Prove the two saved snapshots' old relationship, never today's Rest."""
    from . import animation_worklist_target as target, unity_animation as ua, limb_ik, skirt_rig
    names = target._validated_names(mapped_names)
    # The pure policy schema validator also excludes bool-as-coordinate and
    # malformed finite data, before the native comparison helper is used.
    policy._snapshot(previous, 'Historical artist Rest')
    policy._snapshot(motion_rest, 'Historical motion Rest')
    _require(set(previous) == set(motion_rest) and names <= set(previous),
             'The historical mapped names or motion bone inventory changed.')
    for name, old in previous.items():
        now = motion_rest[name]
        _require(ua._same_current_rest_bone(old, now),
                 'The saved historical motion changed native Rest geometry: ' + name)
        if old['use_connect'] != now['use_connect']:
            original_bone = rig.data.bones.get(name)
            motion_bone = motion.bones.get(name) if motion is not None else original_bone
            _require(old['use_connect'] and not now['use_connect'] and name in names
                     and original_bone is not None and motion_bone is not None
                     and not any(b.get(key) is not None for b in (original_bone, motion_bone)
                                 for key in (limb_ik.OWNER_KEY, skirt_rig.OWNER_KEY)),
                     'An unapproved historical connected-bone flag changed: ' + name)


def _history(rig):
    """Authenticate passive references, without granting historical Rest use."""
    from . import animation_worklist_target as target, unity_animation as ua
    from . import body_region
    _actual_binding(rig)
    _require(target.is_adopted(rig), 'This acceptance is only for an adopted same-data Main Rig.')
    record = target._load(rig)
    _require(len(record['capture']['targets']) == 1,
             'Accept leg Rest requires one exact adopted Main Rig target.')
    captured = record['capture']['targets'][0]
    _require(captured['target'] is rig and captured['data'] is rig.data
             and record['motion_data'] is rig.data,
             'The current, saved artist and adopted motion data must be the same ID.')
    _require(ua._compile_saved_rest_state(captured['rest']) is not None
             and ua._compile_saved_rest_state(record['motion_rest']) is not None,
             'Keep the complete historical Main Rig Rest records before accepting new geometry.')
    _historical_pair(rig, captured['rest'], record['motion_rest'], record['mapped_names'])
    encoded = json.loads(rig[target.SESSION])
    _require(set(rig.get(target.REFS, {})) == target._encoded_reference_keys(encoded),
             'The saved Main Rig reference table changed.')
    _require(target._data_reference_keys(rig, rig.data) == {target.REFS},
             'An unknown rig property refers to the Armature data.')
    owners = bpy.data.user_map().get(rig.data, set())
    _require(rig in owners, 'The original Object is missing from the data owner map.')
    actions, motions = [], {}
    for owner in owners:
        if owner is rig:
            continue
        _require(isinstance(owner, bpy.types.Action) and not owner.library
                 and owner.get(target.ACTION_RIG_KEY) is rig
                 and owner.get(ua.ORIGINAL_DATA_KEY) is rig.data
                 and target._data_reference_keys(owner, rig.data)
                     <= {ua.ORIGINAL_DATA_KEY, ua.PREVIEW_DATA_KEY},
                 'An unknown owner refers to this Armature data; keep its rig intact.')
        try:
            proof = json.loads(owner[target.ACTION_MOTION_KEY])
            raw = owner[ua.ORIGINAL_REST_KEY]
            old = json.loads(raw)
            motion = owner[ua.PREVIEW_DATA_KEY]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('A retained Action lost its historical motion proof.') from exc
        _require(type(proof) is dict
                 and set(proof) == {'version', 'mapped_names', 'motion_rest', 'original_rest_sha256'}
                 and type(proof['version']) is int and proof['version'] == 1
                 and isinstance(raw, str)
                 and proof['original_rest_sha256'] == hashlib.sha256(raw.encode('utf-8')).hexdigest()
                 and ua._compile_saved_rest_state(old) is not None,
                 'A retained Action historical Rest proof is invalid.')
        _require(isinstance(motion, bpy.types.Armature) and motion is not rig.data
                 and not motion.library and not motion.override_library
                 and ua._read_matched_rest_state(motion, proof['motion_rest']) is not None,
                 'Keep the exact separate historical Action motion data unchanged.')
        _historical_pair(rig, old, proof['motion_rest'], proof['mapped_names'], motion)
        actions.append(body_region._action_owner(owner))
        motions[motion.name_full] = {
            'rest_sha256': _hash(ua._rest_state(motion)),
            'properties_sha256': _hash(dict(motion.items()))}
    _require(actions, 'No retained Action references explain the extra data users.')
    descriptor = {
        'actions': sorted(actions, key=lambda row: row['name']), 'motions': motions,
        'fake_user': bool(rig.data.use_fake_user),
        'adopted_history': {'session_sha256': _hash(rig[target.SESSION]),
                            'references_sha256': _hash(dict(rig[target.REFS].items()))}}
    return record, descriptor


def recovery_owners(rig):
    """Explain data users for the explicit revision; never call old Rest passed."""
    from . import body_region
    _require(has_revision(rig), 'No explicit current-controls Rest revision exists.')
    proof = json.loads(rig.data[body_region.RECOVERY_OWNER_KEY])
    raw_audit = rig.data.get(AUDIT_KEY)
    _require(isinstance(raw_audit, str) and proof.get('audit_sha256') == _hash(raw_audit),
             'The explicit current-Rest acceptance history changed.')
    audit = json.loads(raw_audit)
    owners = _history(rig)[1]
    _require(audit.get('version') == 1 and audit.get('historical_owners') == owners,
             'The accepted revision no longer owns these historical animation references.')
    return owners


def verified_revision(rig):
    """Check the persistent proof, without borrowing our in-flight admission."""
    global _ACTIVE
    from . import body_region
    previous = _ACTIVE
    _ACTIVE = None
    try:
        return has_revision(rig) and body_region.verified_single_armature_user(rig)
    finally:
        _ACTIVE = previous


def _native_rest(rig):
    from . import control_pose_assets
    return control_pose_assets.native_rest(rig)


def active_owner(rig):
    """One synchronous transaction may cross its owned temporary graph states."""
    active = _ACTIVE
    if active is None or active['rig'] is not rig or active['data'] is not rig.data:
        return False
    try:
        _actual_binding(rig)
        return _history(rig)[1] == active['owners'] and _native_rest(rig) == active['native_rest']
    except (ValueError, TypeError, KeyError, AttributeError, ReferenceError):
        return False


def checkpoint_rest_view(rig):
    """Only this preflighted transaction may snapshot its still-old Direct graph.

    Once the Direct registry changes, ordinary inventory is strict again. If
    rollback restores that exact old registry, the same current-state snapshot
    can be authenticated until the owning transaction exits.
    """
    from . import limb_ik, unity_animation as ua
    active = _ACTIVE
    if active is None or active['rig'] is not rig or active['data'] is not rig.data:
        return None
    if active.get('capture_view') is None or rig.data.get(limb_ik.DIRECT_REST_KEY) != active.get('registry_raw'):
        return None
    _require(active_owner(rig) and ua._rest_state(rig.data) == active['full_rest'],
             'The preflighted current Rest changed before its rollback checkpoint.')
    return active['capture_view']


@contextmanager
def _admission(rig, owners, native_rest, *, capture_view=None, full_rest=None, registry_raw=None):
    global _ACTIVE
    _require(_ACTIVE is None, 'Another current-Rest acceptance transaction is active.')
    _ACTIVE = {'rig': rig, 'data': rig.data, 'owners': owners, 'native_rest': native_rest,
               'capture_view': capture_view, 'full_rest': full_rest, 'registry_raw': registry_raw}
    try:
        _require(active_owner(rig), 'Current Rest or historical references changed before acceptance.')
        yield
    finally:
        _ACTIVE = None


class _CapturedRestView:
    """Compare only Direct applied fields against the authenticated old capture.

    This is not an Original session. All other graph checks read current RNA.
    The object is private to the explicit acceptance preflight.
    """
    def __init__(self, rig, previous, changed):
        self.rig, self.data = rig, rig.data
        self.previous = previous
        self.changed = frozenset(changed)

    def matches(self, bone, state):
        from . import limb_ik
        if bone is None or bone.name not in self.changed:
            return limb_ik._rest_state_matches(bone, state)
        _require(bone.id_data is self.data and self.rig.data is self.data,
                 'The historical comparison belongs to another Armature data ID.')
        old = self.previous[bone.name]
        head, tail = Vector(old['head']), Vector(old['tail'])
        expected_z = Vector(state['z'])
        matrix = old['matrix']
        z_axis = Vector((matrix[2], matrix[6], matrix[10]))
        tolerance = max((tail-head).length, 1.0) * 1e-6
        return (min(expected_z.length, z_axis.length) > limb_ik.EPSILON
                and (head-Vector(state['head'])).length <= tolerance
                and (tail-Vector(state['tail'])).length <= tolerance
                and z_axis.normalized().dot(expected_z.normalized()) >= 1.0-1e-6
                and (old['parent'] or '') == state['parent']
                and old['use_connect'] == state['use_connect'])

    def details(self, bone, state):
        return 'The authenticated pre-edit Main Rig capture does not match the Direct record.'


def _author_inputs(rig):
    """Hash source meshes, paint and every non-output Key without large reports."""
    from . import forearm_twist
    result = {}
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or not any(m.type == 'ARMATURE' and m.object is rig for m in obj.modifiers):
            continue
        digest = hashlib.sha256()
        mesh = obj.data
        for vertex in mesh.vertices:
            digest.update(struct.pack('<IfffI', vertex.index, *vertex.co, len(vertex.groups)))
            for group in vertex.groups:
                digest.update(struct.pack('<If', group.group, group.weight))
        digest.update(repr(tuple(tuple(e.vertices) for e in mesh.edges)).encode())
        digest.update(repr(tuple(tuple(p.vertices) for p in mesh.polygons)).encode())
        digest.update(repr(tuple((g.name, g.index, g.lock_weight) for g in obj.vertex_groups)).encode())
        owned = {r['key'] for r in forearm_twist._records(obj).values()} if forearm_twist.RECORD_KEY in obj else set()
        if mesh.shape_keys:
            for key in mesh.shape_keys.key_blocks:
                digest.update(repr((key.as_pointer(), key.name, key.relative_key.as_pointer(),
                    key.interpolation, key.slider_min, key.slider_max, key.vertex_group,
                    getattr(key, 'lock_shape', False))).encode())
                if key.name not in owned:
                    digest.update(repr((key.value, key.mute)).encode())
                    for point in key.data:
                        digest.update(struct.pack('<fff', *point.co))
        result[obj.as_pointer()] = (mesh.as_pointer(), digest.hexdigest())
    return result


def prepare(context, rig):
    """Read-only plan for both existing Direct legs; no generated graph changes."""
    from . import (animation_worklist_target as target, body_original_mode, body_region,
                   body_rest_resync, foot_controls, limb_ik, unity_animation as ua)
    _actual_binding(rig)
    _require(not has_revision(rig),
             'These leg proportions already have an accepted control revision. Use Release to Edit before the next Rest adjustment.')
    _require(context.object is rig and context.mode in {'OBJECT', 'POSE'}
             and not (context.screen and context.screen.is_animation_playing),
             'Pause playback and make the Main Rig active in Object or Pose Mode.')
    _require(body_original_mode.SESSION not in rig,
             'Finish the existing Original session through its own Rest resync first.')
    _require([s for s in bpy.data.scenes if s.objects.get(rig.name) is rig] == [context.scene]
             and context.view_layer.objects.get(rig.name) is rig,
             'Use the original single scene and view layer for the Main Rig.')
    _require(rig.data.get(limb_ik.SCHEMA_KEY) == limb_ik.DIRECT_PREROLL_SCHEMA,
             'Accept current leg Rest supports current Direct controls only.')
    _require(body_region.state(rig, 'LEGS')['status'] == 'BOUND'
             and not body_region._load(rig),
             'Both legs must have their complete existing controls and no open region release.')
    record, owners = _history(rig)
    previous = record['capture']['targets'][0]['rest']
    current = ua._rest_state(rig.data)
    registry = limb_ik._load_direct_rest_registry(rig, strict=True)
    legs = {entry['side']: entry for entry in registry['limbs'].values() if entry['kind'] == 'LEG'}
    _require(set(legs) == {'L', 'R'}, 'Both historical Direct leg chains are required.')
    feet = foot_controls.records(rig)
    _require(set(feet) == {'L', 'R'}, 'Both original Foot control records are required.')
    chains = {side: (*legs[side]['chain'], feet[side]['toe']) for side in ('L', 'R')}
    companions = {feet[side]['bones'][role] for side in ('L', 'R') for role in _COMPANION_ROLES}
    changes = policy.classify_changes(previous, current, leg_chains=chains,
        owned_companions=companions, same_rest_bone=ua._same_current_rest_bone)
    _require(changes.native_changed, 'No supported native leg Rest geometry changed.')
    # A view can substitute only the changed native Direct source comparisons.
    # Full Foot ownership, helper geometry, drivers, constraints and widgets stay strict.
    view = _CapturedRestView(rig, previous, changes.native_changed)
    inventory = limb_ik._validate_inventory(rig, original_rest=view)
    _require(all(tuple(inventory['rigs'][('LEG', side)]['chain']) == tuple(legs[side]['chain'])
                 for side in ('L', 'R')), 'The current leg graph no longer matches its recorded chains.')
    body_rest_resync._refuse_dependencies(rig, inventory, {('LEG', 'L'), ('LEG', 'R')})
    affected = set()
    for side in ('L', 'R'):
        key = ('LEG', side)
        affected.update(body_region._native_scope(rig, inventory['rigs'][key]['chain']))
        affected.update(feet[side]['bones'].values())
        affected.update(b.name for b in inventory['bones']
                        if (b.get(limb_ik.KIND_KEY), b.get(limb_ik.SIDE_KEY)) == key)
    body_region._animations(rig, affected, limb_ik_fk_paths(rig))
    new_registry = copy.deepcopy(registry)
    for entry in new_registry['limbs'].values():
        if entry['kind'] != 'LEG':
            continue
        states = {name: body_rest_resync._current_direct_state(rig, name) for name in entry['chain'][:2]}
        first, second = (states[name] for name in entry['chain'][:2])
        _require(second['parent'] == entry['chain'][0]
                 and (Vector(first['tail'])-Vector(second['head'])).length
                     <= max((Vector(first['tail'])-Vector(first['head'])).length,
                            (Vector(second['tail'])-Vector(second['head'])).length, 1.0)*1e-6,
                 'The edited native knee is disconnected; keep its current geometry for review.')
        entry['original'] = copy.deepcopy(states)
        entry['applied'] = copy.deepcopy(states)
    return {'rig': rig, 'data': rig.data, 'owners': owners, 'native_rest': _native_rest(rig),
            'full_rest': current, 'capture_view': view, 'registry_raw': rig.data.get(limb_ik.DIRECT_REST_KEY),
            'owner_raw': rig.data.get(body_region.RECOVERY_OWNER_KEY), 'audit_raw': rig.data.get(AUDIT_KEY),
            'registry': new_registry, 'native_changed': sorted(changes.native_changed),
            'companion_changed': sorted(changes.companion_changed), 'author_inputs': _author_inputs(rig),
            'correctives': body_rest_resync._corrective_checkpoint(rig)}


def limb_ik_fk_paths(rig):
    from . import limb_ik_fk, foot_controls
    return limb_ik_fk.owned_driver_paths(rig) | foot_controls.owned_driver_paths(rig)


def accept_current_legs(context, rig, *, rebuild=True):
    """Accept and optionally rebuild in one current-state rollback boundary.

    Persistent writes are the two-leg Direct registry, separate owner revision
    and audit event. Existing regional rebuild owns its generated controls.
    No old Action/capture/SESSION/REFS payload is rewritten or marked restored.
    """
    from . import (body_region, body_rest_resync, body_setup, forearm_twist,
                   limb_ik, unity_animation as ua)
    plan = prepare(context, rig)
    old_native = plan['native_rest']
    with _admission(rig, plan['owners'], old_native, capture_view=plan['capture_view'],
                    full_rest=plan['full_rest'], registry_raw=plan['registry_raw']), \
            forearm_twist.defer_runtime(context, flush_on_exit=False):
        def commit():
            _require(rig.data is plan['data'] and _history(rig)[1] == plan['owners']
                     and _native_rest(rig) == old_native
                     and ua._rest_state(rig.data) == plan['full_rest']
                     and _author_inputs(rig) == plan['author_inputs']
                     and rig.data.get(limb_ik.DIRECT_REST_KEY) == plan['registry_raw']
                     and rig.data.get(body_region.RECOVERY_OWNER_KEY) == plan['owner_raw']
                     and rig.data.get(AUDIT_KEY) == plan['audit_raw'],
                     'The accepted Rest plan changed; inspect the latest artist state again.')
            event = {'version': 1, 'accepted_utc': datetime.now(timezone.utc).isoformat(),
                     'native_changed': plan['native_changed'], 'companion_changed': plan['companion_changed'],
                     'prior_direct_registry_raw': plan['registry_raw'],
                     'prior_owner_revision_raw': plan['owner_raw'], 'prior_audit_raw': plan['audit_raw'],
                     'historical_owners': plan['owners'], 'accepted_native_rest_sha256': _hash(old_native)}
            rig.data[AUDIT_KEY] = _canonical(event)
            limb_ik._write_direct_rest_registry(rig, plan['registry'])
            proof = {'version': 1, 'mode': MODE, 'rig': rig.name_full, 'data': rig.data.name_full,
                     'armature_id': rig.data.get(limb_ik.ARMATURE_ID_KEY), 'owners': plan['owners'],
                     'rest': ua._rest_state(rig.data),
                     'bone_properties': {b.name: _hash(body_region._bone_ownership(b)) for b in rig.data.bones},
                     'audit_sha256': _hash(rig.data[AUDIT_KEY])}
            rig.data[body_region.RECOVERY_OWNER_KEY] = _canonical(proof)
            limb_ik._validate_inventory(rig)
            result = body_region.rebuild(context, rig, 'LEGS') if rebuild else {'accepted': ['LEG.L', 'LEG.R']}
            _require(_native_rest(rig) == old_native, 'Accepting leg Rest changed original native bone geometry.')
            _require(_history(rig)[1] == plan['owners'], 'Historical animation references changed during acceptance.')
            _require(verified_revision(rig), 'The final current-controls owner proof failed.')
            depsgraph = context.evaluated_depsgraph_get()
            context.view_layer.update()
            for state in plan['correctives']:
                obj = state['object']
                # Checkpoint every bound output, but refresh only this Scene as
                # update_runtime does. Hidden/excluded Scene members still count.
                if context.scene.objects.get(obj.name) != obj:
                    continue
                forearm_twist._CACHE.pop(obj.as_pointer(), None)
                forearm_twist._OUTPUT_CACHE.pop(obj.as_pointer(), None)
                forearm_twist._calculate_object(obj, depsgraph)
                _require(obj.name not in forearm_twist._ERRORS,
                         'The existing Forearm correction did not resume; keep the current Rest and review its profile.')
            _require(_author_inputs(rig) == plan['author_inputs'],
                     'Source mesh, paint or an author Shape Key changed; the acceptance was rolled back.')
            return dict(result, native_rest_preserved=True, historical_actions_unchanged=True,
                        old_animation_compatibility_granted=False)
        try:
            return body_setup._atomic(context, rig, commit)
        except Exception:
            # Existing _atomic restores the current author Rest and custom data.
            # Restore runtime-owned outputs only after its last rollback flush.
            body_rest_resync.restore_correctives(plan)
            raise
