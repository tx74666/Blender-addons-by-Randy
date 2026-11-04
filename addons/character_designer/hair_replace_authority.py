"""Current Hair topology revisions, without rewriting historical Main recovery.

The caller owns native bones, paint and its replacement Action.  This module
owns only the fresh authority checkpoint, a passive bone backup, and current
worklist membership.  Roll native changes back before calling ``rollback``.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import uuid

import bpy
from idprop.types import IDPropertyArray, IDPropertyGroup
from mathutils import Color, Euler, Matrix, Quaternion, Vector

RECORD = 'character_designer_hair_current_topology_v1'
REFS = 'character_designer_hair_current_topology_refs_v1'
SCHEMA = 'character-designer.current-hair-topology/1'
_PENDING = {}


def _require(value, message):
    if not value:
        raise ValueError(message)


def _modules():
    from . import animation_worklist_revision as revision
    from . import hair_bones_rig as hair, body_rest_adoption as adoption
    return revision, revision._modules(), hair, adoption


def _names(values):
    _require(not isinstance(values, (str, bytes)), 'Choose complete owned Hair strands.')
    values = tuple(values)
    _require(values and all(type(name) is str and name for name in values)
             and len(set(values)) == len(values), 'The Hair bone scope is empty or duplicated.')
    return frozenset(values)


def _primitive(value, path='$'):
    """Read native author values explicitly; never traverse arbitrary RNA."""
    if isinstance(value, bpy.types.ID):
        return {'$id': [value.bl_rna.identifier, value.name_full,
                        value.library.filepath if value.library else None]}
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        _require(math.isfinite(value), 'Nonfinite Hair authority value at ' + path)
        return value
    if isinstance(value, Matrix):
        return {'$matrix': [[_primitive(float(item), path + '[' + str(i) + '][' + str(j) + ']')
                             for j, item in enumerate(row)] for i, row in enumerate(value)]}
    for cls, tag in ((Vector, '$vector'), (Quaternion, '$quaternion'),
                     (Euler, '$euler'), (Color, '$color')):
        if isinstance(value, cls):
            result = {tag: [_primitive(float(item), path + '[' + str(i) + ']')
                            for i, item in enumerate(value)]}
            if isinstance(value, Euler):
                result['order'] = value.order
            return result
    if isinstance(value, IDPropertyArray):
        return {'$id_array': [_primitive(item, path + '[' + str(i) + ']')
                              for i, item in enumerate(value)], 'typecode': value.typecode}
    if (type(value).__name__ == 'bpy_prop_array'
            and type(value).__module__ in {'bpy', 'bpy_types', 'builtins'}):
        return {'$rna_array': [_primitive(item, path + '[' + str(i) + ']')
                               for i, item in enumerate(value)]}
    if isinstance(value, (dict, IDPropertyGroup, bpy.types.PropertyGroup)):
        _require(all(type(key) is str for key in value.keys()),
                 'Unsupported Hair authority mapping key at ' + path)
        items = {key: _primitive(item, path + '[' + json.dumps(key) + ']')
                 for key, item in value.items()}
        if isinstance(value, bpy.types.PropertyGroup):
            return {'$rna_group': value.bl_rna.identifier, 'items': items}
        return items
    if isinstance(value, (tuple, list)):
        return [_primitive(item, path + '[' + str(i) + ']') for i, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        items = [_primitive(item, path + '{item}') for item in value]
        return {'$set': sorted(items, key=lambda item: json.dumps(item, sort_keys=True, allow_nan=False))}
    raise ValueError('Unsupported Hair authority value at ' + path + ': '
                     + type(value).__module__ + '.' + type(value).__qualname__)


def _native_hash(value, path='$'):
    encoded = json.dumps(_primitive(value, path), ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _properties(owner, body=None):
    path = getattr(owner, 'name_full', None) or getattr(owner, 'name', type(owner).__name__)
    return _native_hash(dict(owner.items()), str(path) + '.IDProperties')


def _constraint_state(pose, body):
    result = []
    for constraint in pose.constraints:
        fields = {'type': constraint.type}
        for prop in constraint.bl_rna.properties:
            name = prop.identifier
            # Solver error/residual/validity fields are evaluated observations,
            # not an authored dependency graph. Pin all writable author fields.
            if name == 'rna_type' or prop.is_readonly or prop.type == 'COLLECTION':
                continue
            try:
                value = getattr(constraint, name)
                if isinstance(value, bpy.types.ID):
                    value = ('ID', value.bl_rna.identifier, str(value.session_uid))
                elif getattr(prop, 'is_array', False):
                    value = list(value)
                elif prop.type == 'POINTER':
                    value = None if value is None else str(value.as_pointer())
                fields[name] = value
            except (AttributeError, TypeError, ValueError):
                raise ValueError('A Hair replacement cannot authenticate a constraint field: ' + name) from None
        fields['targets'] = [(getattr(item, 'target', None), getattr(item, 'subtarget', ''),
                             getattr(item, 'weight', None))
                            for item in getattr(constraint, 'targets', ())]
        result.append(fields)
    return _native_hash(result, pose.name + '.constraints')


def _graph(rig, excluded, api):
    """All surviving bone authorship, including constraints beyond name/type."""
    result = {}
    for bone in rig.data.bones:
        if bone.name in excluded:
            continue
        pose = rig.pose.bones[bone.name]
        result[bone.name] = dict(properties=_properties(bone, api.body),
            pose_properties=_properties(pose, api.body), constraints=_constraint_state(pose, api.body),
            locks={key: tuple(getattr(pose, key)) if hasattr(getattr(pose, key), '__len__')
                   else getattr(pose, key) for key in api.target._LOCKS},
            rotation_mode=pose.rotation_mode)
    drivers = []
    for owner in (rig, rig.data):
        ad = owner.animation_data
        drivers.append(_native_hash([(curve.data_path, curve.array_index,
            curve.driver.type, curve.driver.expression,
            [(variable.name, variable.type,
              [(target.id, target.bone_target, target.data_path, target.transform_type,
                target.transform_space) for target in variable.targets])
             for variable in curve.driver.variables]) for curve in ad.drivers],
             owner.name_full + '.drivers') if ad else None)
    return result, drivers


def _history_unchanged(plan):
    rig, api = plan['rig'], plan['api']
    _require(rig.data is plan['data'] and rig[api.target.SESSION] == plan['history_raw']
             and dict(rig[api.target.REFS]) == plan['history_refs'],
             'The historical Main Rig identity or recovery record changed.')


def _action_witness(action, slot, revision, api):
    native_slot = api.worklist._slot(action, slot)
    return (action, slot, action.name_full, bool(action.use_fake_user),
            _properties(action, api.body),
            (native_slot.handle, native_slot.identifier, native_slot.target_id_type))


def _verify_actions(plan):
    revision, api = plan['revision'], plan['api']
    for witness in plan['actions']:
        action, slot = witness[:2]
        _require(_action_witness(action, slot, revision, api) == witness,
                 'A retained Source/Custom Action identity, slot or metadata changed during Hair replacement.')
    for checkpoint, raw, refs in plan['checkpoints']:
        _require(checkpoint.get(revision.RECORD) == raw
                 and dict(checkpoint.get(revision.REFS, {})) == refs,
                 'A retained current-Rig checkpoint changed during Hair replacement.')


def _backup_description(data, api):
    return dict(rest=api.ua._rest_state(data),
                properties={bone.name: _properties(bone, api.body) for bone in data.bones})


def _read_basic(rig):
    """No adoption callback here: historical ownership uses this reader too."""
    raw = rig.get(RECORD)
    if raw is None:
        return None
    revision, api, hair, adoption = _modules()
    _require(type(raw) is str and hasattr(rig.get(REFS), 'items'),
             'The current Hair topology authority is incomplete.')
    value = json.loads(raw)
    _require(type(value) is dict and set(value) == {'payload', 'sha256'}
             and value['sha256'] == revision._digest(value['payload']),
             'The current Hair topology checkpoint changed.')
    value, refs = value['payload'], dict(rig[REFS])
    history = dict(rig.get(api.target.REFS, {}))
    _require(value.get('schema') == SCHEMA and refs.get('rig') is rig
             and history.get(value.get('data_history_key')) is rig.data
             and api.body._hash(rig.get(api.target.SESSION)) == value['history_sha256']
             and api.body._hash(dict(rig.get(api.target.REFS, {}))) == value['history_refs_sha256']
             and rig.data.get(api.body.RECOVERY_OWNER_KEY) == value['owner_raw']
             and rig.data.get(adoption.AUDIT_KEY) == value['audit_raw'],
             'The current Hair topology lost its exact Main Rig or Body owner.')
    _require(api.ua._rest_state(rig.data) == value['current_rest']
             and api.pose._structure(rig) == value['current_structure'],
             'Current Hair topology Rest or the control graph changed.')
    _require(set(refs) == {'rig', 'checkpoint',
                         *('backup_' + str(i) for i in range(len(value['backups'])))},
             'The current Hair topology reference inventory changed.')
    for index, saved in enumerate(value['backups']):
        backup = refs['backup_' + str(index)]
        _require(isinstance(backup, bpy.types.Armature) and backup is not rig.data
                 and backup.library is None and backup.override_library is None
                 and backup.use_fake_user and not any(obj.type == 'ARMATURE' and obj.data is backup
                                                     for obj in bpy.data.objects)
                 and _backup_description(backup, api) == saved['snapshot'],
                 'The passive Hair bone backup changed or gained a scene binding.')
    checkpoint = refs['checkpoint']
    _require(isinstance(checkpoint, bpy.types.Action) and checkpoint.library is None
             and checkpoint.use_fake_user, 'The current Hair work checkpoint is missing.')
    envelope = json.loads(checkpoint[revision.RECORD])
    _require(set(envelope) == {'payload', 'sha256'}
             and envelope['sha256'] == revision._digest(envelope['payload'])
             and envelope['sha256'] == value['checkpoint_sha256'],
             'The current Hair work checkpoint content changed.')
    pending = _PENDING.get(checkpoint.as_pointer())
    if pending is not None:
        _require(pending['rig'] is rig and pending['data'] is rig.data
                 and pending.get('written') and not pending.get('committed'),
                 'The synchronous Hair authority owner changed.')
    else:
        checkpoint_refs = dict(checkpoint[revision.REFS])
        _require(all(isinstance(item, bpy.types.ID) and not isinstance(item, bpy.types.Armature)
                     and item is not checkpoint for item in checkpoint_refs.values()),
                 'The current Hair work checkpoint gained unsupported native references.')
        current = revision._decode(envelope['payload'], checkpoint_refs, history, rig)
        _require(current['schema'] == revision.SCHEMA and current['rig'] is rig
                 and current['history_raw'] == rig[api.target.SESSION]
                 and current['history_refs'] == history
                 and current['current_rest'] == value['current_rest']
                 and current['current_structure'] == value['current_structure'],
                 'The current Hair work checkpoint lost its historical/current scope.')
        rows = [row for row in current['rows'] if row['item_id'] == value['item_id']]
        _require(len(rows) == 1, 'The current Hair work checkpoint lost its exact row.')
        row = rows[0]
        custom = row['custom_action']
        _require(isinstance(custom, bpy.types.Action) and custom.library is None
                 and custom.get(revision.CHECKPOINT) is checkpoint
                 and custom.get(revision.TOKEN) == row['current_rig_revision']
                 and row['rig'] is rig
                 and not any(key in custom for key in api.target._action_motion_keys()),
                 'The current Hair Custom lost its checkpoint identity or gained old recovery permissions.')
        api.worklist._slot(custom, row['custom_slot'])
    return value, refs


def historical_bones(rig):
    """Authenticate removed native bones once for one synchronous proof call.

    This supplies provenance for a historical connected-flag proof. It never
    grants historical Rest, Action activation, or a restoration permission.
    The caller must not cache this mapping across validation calls or events.
    """
    saved = _read_basic(rig)
    if saved is None:
        return {}
    value, refs = saved
    result = {}
    for index, entry in enumerate(value['backups']):
        for name in entry['removed']:
            bone = refs['backup_' + str(index)].bones.get(name)
            _require(bone is not None, 'The historical Hair bone backup is missing.')
            # Preserve the first genuine historical occurrence if later
            # replacements reuse a prior generated name.
            result.setdefault(name, bone)
    return result


def historical_bone(rig, name):
    """Convenience lookup; bulk validators should call historical_bones once."""
    return historical_bones(rig).get(name)


class _CurrentTopologyView:
    def __init__(self, rig):
        self.rig, self.data = rig, rig.data

    def source_bone(self, bone):
        current = self.data.bones.get(bone.name) if bone is not None else None
        _require(self.rig.data is self.data and bone is not None
                 and bone.id_data == self.data and current is not None
                 and current.as_pointer() == bone.as_pointer(),
                 'The Hair bone is outside the validated current topology.')
        return bone


def current_topology_view(rig, context=None):
    """Fresh complete current revision proof, separate from old Main Rest."""
    saved = _read_basic(rig)
    if saved is None:
        return None
    value, refs = saved
    _revision, _api, _hair, adoption = _modules()
    _require(adoption.verified_revision(rig), 'The current Body/Hair owner proof did not validate.')
    # This is Rest authority only. Full Action activation still uses the normal
    # revision reader; Original's authenticated muted display may read Hair.
    return _CurrentTopologyView(rig)


def prove_missing_mapped(rig, missing_names):
    """Permit current control reads only for authenticated removed Hair names.

    This does not alter the historical mapping, validate an old Action, or
    grant recovery rights. Separate protected/motion data keep the old gate.
    """
    missing = _names(missing_names)
    saved = _read_basic(rig)
    if saved is None:
        return False
    _revision, api, hair, adoption = _modules()
    record = api.target._load_rest_record(rig, _for_rest_proof=True)
    original = record['capture']['targets'][0]['data']
    if original is not rig.data or record['motion_data'] is not rig.data:
        return False
    actual_missing = set(record['mapped_names']) - set(rig.data.bones.keys())
    if missing != actual_missing:
        return False
    # Open regional receipts own their old complete protected partition. They
    # cannot acquire a topology exception, or recursively borrow this proof.
    if api.body._load(rig):
        return False
    historical = historical_bones(rig)
    from . import limb_ik, skirt_rig
    for name in missing:
        bone = historical.get(name)
        if bone is None or bone.get(hair.OWNER_KEY) != hair.OWNER_VALUE:
            return False
        source, signature = bone.get(hair.SOURCE_KEY), bone.get(hair.SIGNATURE_KEY)
        if (not isinstance(source, bpy.types.Object) or source.type != 'MESH'
                or source.get(hair.RIG_KEY) is not rig or type(signature) is not str
                or any(bone.get(key) is not None for key in (limb_ik.OWNER_KEY, skirt_rig.OWNER_KEY))):
            return False
        binding = hair._read_records(source)
        if binding is None or signature not in {chain['signature'] for chain in binding['chains']}:
            return False
    return bool(adoption.verified_revision(rig))


def guard_activation(context, item, side):
    """Old Source curves must not silently target removed native Hair bones."""
    if side != 'SOURCE' or _read_basic(item.rig) is None:
        return
    revision, api, _hair, _adoption = _modules()
    action = item.source_action
    slot = api.worklist._slot(action, item.source_slot)
    for curve in api.fingerprint._selected_curves(action, slot.handle):
        try:
            item.rig.path_resolve(curve.data_path)
        except (ValueError, KeyError, AttributeError):
            raise ValueError('This Source uses the previous Hair topology; keep its original draft and use the current Custom.') from None


def _prepare(context, rig, old_names, *, admission_only):
    """Shared fresh admission; a summary never owns a transaction or backup."""
    _require(type(admission_only) is bool, 'Choose an explicit Hair admission mode.')
    revision, api, hair, adoption = _modules()
    old_names = _names(old_names)
    api.worklist._idle(context)
    _require(isinstance(rig, bpy.types.Object) and rig.type == 'ARMATURE'
             and rig.library is None and rig.override_library is None and rig.is_editable
             and rig.data.library is None and rig.data.override_library is None
             and rig.mode != 'EDIT' and rig.data.pose_position == 'POSE'
             and context.scene.objects.get(rig.name) is rig,
             'Hair replacement needs its local Main Rig in Pose position.')
    _require(not api.original.active(rig) and not api.pose.active(rig)
             and not api.composition.active(rig) and not api.ua.active_preview(rig)
             and 'character_designer_rigging_session_v1' not in rig,
             'Finish Original, Pose tests and motion previews before replacing Hair.')
    _require(all(name in rig.data.bones for name in old_names), 'The selected Hair bones changed.')
    sources = {}
    for name in old_names:
        bone = rig.data.bones[name]
        source, signature = bone.get(hair.SOURCE_KEY), bone.get(hair.SIGNATURE_KEY)
        _require(bone.get(hair.OWNER_KEY) == hair.OWNER_VALUE
                 and isinstance(source, bpy.types.Object) and source.type == 'MESH'
                 and source.get(hair.RIG_KEY) is rig and type(signature) is str and signature,
                 'Only complete, authenticated owned Hair bones may be replaced.')
        sources.setdefault(source, set()).add(signature)
    for source, signatures in sources.items():
        record = hair._read_records(source)
        hair._validate_owned(source, rig, record, hair._mesh_snapshot(source))
        expected = {name for chain in record['chains'] if chain['signature'] in signatures
                    for name in chain['bones']}
        _require(expected <= old_names, 'Replace complete Hair chains, not individual bones.')
    plan = dict(kind='hair_replace.authority_plan', context=context, rig=rig, data=rig.data, revision=revision, api=api,
                old_names=old_names, source_signatures=sources,
                old_rest=api.ua._rest_state(rig.data), graph=_graph(rig, old_names, api),
                created_actions=[], created_armatures=[], committed=False, written=False,
                before_actions=frozenset(bpy.data.actions),
                before_libraries=frozenset(bpy.data.libraries), source_receipts=[])
    # Ordinary single-user rigs need no shared-data or worklist exception.
    if not api.target.managed(rig):
        _require(rig.data.users == 1, 'An unmanaged shared Armature cannot replace Hair in place.')
        if admission_only:
            return dict(kind='hair_replace.eligibility', eligible=True, managed=False,
                        selected_bones=len(old_names))
        plan['managed'] = False
        return plan
    _require(api.target.is_adopted(rig) and adoption.verified_revision(rig),
             'Use a validated current-Rig Body revision before replacing adopted Hair.')
    record = api.target._load(rig)
    _require(len(record['capture']['targets']) == 1
             and record['capture']['targets'][0]['target'] is rig
             and record['capture']['targets'][0]['data'] is rig.data
             and record['motion_data'] is rig.data,
             'Hair replacement does not migrate separate protected/motion data sessions.')
    saved = api.worklist.state(context)
    selected = api.worklist.current(context)
    _require(saved.rig is rig and selected is not None and selected.rig is rig
             and bool(selected.current_rig_revision),
             'Choose a validated local current-Rig Custom before replacing Hair.')
    current = revision.validate(context, selected.item_id)
    ad = rig.animation_data
    _require(ad is not None and ad.action is selected.custom_action
             and ad.action_slot_handle == selected.custom_slot and selected.side == 'CUSTOM'
             and not ad.use_tweak_mode and not any(not track.mute and track.strips for track in ad.nla_tracks),
             'Use the current Custom without active NLA before replacing adopted Hair.')
    rows = [revision._row(item) for item in saved.items]
    _require(all(row['rig'] is rig for row in rows)
             and len({row['item_id'] for row in rows}) == len(rows),
             'The current worklist contains mixed or duplicated rig rows.')
    if admission_only:
        # Only reject unsupported contexts before expensive sampling. The
        # physical replacement later calls prepare afresh; no witness, native
        # backup, or authority plan crosses the sampling interval.
        _read_basic(rig)
        adoption.recovery_owners(rig)
        return dict(kind='hair_replace.eligibility', eligible=True, managed=True,
                    selected_bones=len(old_names))
    witnesses, checkpoints, seen, seen_checkpoints = [], [], set(), set()
    for row in rows:
        for action_key, slot_key in (('source_action', 'source_slot'), ('custom_action', 'custom_slot')):
            action, handle = row[action_key], row[slot_key]
            if action is not None and (action.as_pointer(), handle) not in seen:
                witnesses.append(_action_witness(action, handle, revision, api))
                seen.add((action.as_pointer(), handle))
            checkpoint = action.get(revision.CHECKPOINT) if action and not action.library else None
            if isinstance(checkpoint, bpy.types.Action) and checkpoint not in seen_checkpoints:
                checkpoints.append((checkpoint, checkpoint[revision.RECORD], dict(checkpoint[revision.REFS])))
                seen_checkpoints.add(checkpoint)
    prior = _read_basic(rig)
    owner_raw, audit_raw = rig.data[api.body.RECOVERY_OWNER_KEY], rig.data[adoption.AUDIT_KEY]
    plan.update(managed=True, saved=saved, selected_id=selected.item_id,
        selected_row=revision._row(selected), rows=rows, row_rna=selected.bl_rna,
        previous_index=saved.active_index, previous_status=saved.status, previous_error=saved.has_error,
        history_raw=rig[api.target.SESSION], history_refs=dict(rig[api.target.REFS]),
        owners=adoption.recovery_owners(rig), owner_raw=owner_raw, audit_raw=audit_raw,
        old_record=(RECORD in rig, rig.get(RECORD)), old_refs=(REFS in rig, dict(rig.get(REFS, {}))),
        actions=witnesses, checkpoints=checkpoints, prior=prior,
        old_action=ad.action, old_slot=ad.action_slot_handle,
        previous_capture=current['capture'])
    # No new IDs until every supported session and row has passed admission.
    backup = rig.data.copy()
    plan['created_armatures'].append(backup)
    try:
        backup.name = rig.data.name + ' · Hair topology recovery'
        backup.animation_data_clear()
        for key in tuple(backup.keys()):
            del backup[key]
        backup.use_fake_user = True
        _require(not api.target._data_reference_keys(backup, rig.data),
                 'The passive Hair copy gained a reference to current Armature data.')
        plan['backup'] = backup
        plan['backup_description'] = _backup_description(backup, api)
        _history_unchanged(plan)
        # Old Action identity/metadata and complete checkpoints are protected;
        # this owned data copy introduces no original Action curve writer.
        _require(adoption.recovery_owners(rig) == plan['owners'],
                 'The passive Hair copy changed the original Armature owner map.')
        return plan
    except Exception:
        backup.use_fake_user = False
        if backup.users == 0:
            bpy.data.armatures.remove(backup, do_unlink=False)
        raise


def admit(context, rig, old_names):
    """Read-only eligibility check; its plain summary grants no commit rights."""
    return _prepare(context, rig, old_names, admission_only=True)


def prepare(context, rig, old_names):
    """Capture fresh full witnesses and an owned backup before native writes."""
    return _prepare(context, rig, old_names, admission_only=False)


def _verify_delta(plan, new_names):
    rig, api, _hair = plan['rig'], plan['api'], _modules()[2]
    _require(rig.data is plan['data'], 'Hair replacement changed the Main Rig data identity.')
    current = api.ua._rest_state(rig.data)
    remaining = set(plan['old_rest']) - plan['old_names']
    _require(set(current) == remaining | set(new_names)
             and not remaining.intersection(new_names),
             'Hair replacement changed bones outside its explicit old/new scope.')
    _require(all(current[name] == plan['old_rest'][name] for name in remaining)
             and _graph(rig, new_names, api) == plan['graph'],
             'Hair replacement changed surviving Body/other Hair Rest, properties or graph.')
    for name in new_names:
        bone = rig.data.bones[name]
        source, signature = bone.get(_hair.SOURCE_KEY), bone.get(_hair.SIGNATURE_KEY)
        _require(bone.get(_hair.OWNER_KEY) == _hair.OWNER_VALUE
                 and source in plan['source_signatures']
                 and signature in plan['source_signatures'][source],
                 'A replacement bone lost its selected Hair source/signature ownership.')
    return current


def _undo_metadata(plan):
    if not plan.get('managed') or not plan.get('written'):
        return
    rig, api, revision = plan['rig'], plan['api'], plan['revision']
    _history_unchanged(plan)
    revision._replace_rows(plan['saved'], plan['rows'])
    saved = plan['saved']
    saved.active_index, saved.status, saved.has_error = (plan['previous_index'],
        plan['previous_status'], plan['previous_error'])
    rig.data[api.body.RECOVERY_OWNER_KEY] = plan['owner_raw']
    rig.data[_modules()[3].AUDIT_KEY] = plan['audit_raw']
    for key, state in ((RECORD, plan['old_record']), (REFS, plan['old_refs'])):
        present, value = state
        if present:
            rig[key] = value
        else:
            rig.pop(key, None)
    action = plan.get('replacement_action')
    checkpoint = plan.get('checkpoint')
    if action is not None and checkpoint is not None:
        if action.get(revision.CHECKPOINT) is checkpoint:
            action.pop(revision.CHECKPOINT, None)
            action.pop(revision.TOKEN, None)
    plan['written'] = plan['committed'] = False


def _live_id(value, inventory):
    try:
        pointer = value.as_pointer()
    except (AttributeError, ReferenceError):
        return None
    return next((item for item in inventory if item.as_pointer() == pointer), None)


def _remove_owned_library(library, plan, expected_path):
    """Remove only our empty Source Library, including its native extra-user.

    Blender 5.2 creates a Library with users=1/use_extra_user=True even after
    its last linked ID is removed. That intrinsic user is not a foreign ID.
    Never clear counters/flags, and prove all actual references absent first.
    """
    _require(isinstance(library, bpy.types.Library)
             and library not in plan['before_libraries']
             and library.library is None and library.parent is None
             and Path(bpy.path.abspath(library.filepath)).resolve() == Path(expected_path).resolve(),
             'The new Hair Source Library lost its exact transaction ownership.')
    _require(not bpy.data.user_map(subset={library}).get(library, set()),
             'A native ID still references the new Hair Source Library; it was kept.')
    for prop in bpy.data.bl_rna.properties:
        if prop.type == 'COLLECTION':
            _require(not any(isinstance(value, bpy.types.ID) and value.library is library
                             for value in getattr(bpy.data, prop.identifier)),
                     'The new Hair Source Library still contains linked native IDs; it was kept.')
    _require(not any(other.parent is library for other in bpy.data.libraries),
             'Another Library depends on the new Hair Source Library; it was kept.')
    _require(not library.use_fake_user
             and library.users == int(bool(library.use_extra_user)),
             'The new Hair Source Library has an unexplained user; it and its cache were kept.')
    # No real users/linked IDs remain. do_unlink permits Blender to dispose its
    # own intrinsic extra-user without user_clear or changing anyone's flags.
    bpy.data.libraries.remove(library, do_unlink=True)


def _cleanup_created_actions(plan):
    """Dispose only this transaction's IDs before removing its new cache file."""
    revision = plan['revision']
    errors, actions, libraries = [], [], []
    def attempt(call):
        try:
            call()
        except Exception as error:
            errors.append(str(error))
    checkpoint = _live_id(plan.get('checkpoint'), bpy.data.actions)
    if checkpoint is not None:
        attempt(lambda: checkpoint.pop(revision.REFS, None))
    for value in reversed(plan['created_actions']):
        action = _live_id(value, bpy.data.actions)
        if action is not None and action not in actions:
            actions.append(action)
    for receipt in plan['source_receipts']:
        loaded = receipt.get('load_result')
        candidates = [receipt.get('action')]
        if loaded is not None:
            candidates.extend(getattr(loaded, 'actions', ()))
        for value in candidates:
            action = _live_id(value, bpy.data.actions)
            if action is None or action in plan['before_actions']:
                continue
            if action not in actions:
                actions.append(action)
            library = action.library
            if (library is not None and library not in plan['before_libraries']
                    and str(Path(bpy.path.abspath(library.filepath)).resolve())
                    == str(Path(receipt['path']).resolve()) and library not in libraries):
                libraries.append(library)
        library = _live_id(receipt.get('library'), bpy.data.libraries)
        if (library is not None and library not in plan['before_libraries']
                and str(Path(bpy.path.abspath(library.filepath)).resolve())
                == str(Path(receipt['path']).resolve()) and library not in libraries):
            libraries.append(library)
    for action in actions:
        _require(action not in plan['before_actions'], 'A preserved Action entered the Hair cleanup inventory.')
        attempt(lambda action=action: revision._remove_action(action))
    for library in libraries:
        expected_path = next(receipt['path'] for receipt in plan['source_receipts']
            if receipt.get('path') and Path(bpy.path.abspath(library.filepath)).resolve()
            == Path(receipt['path']).resolve())
        attempt(lambda library=library, expected_path=expected_path:
                _remove_owned_library(library, plan, expected_path))
    # A foreign native user keeps its cache, including a partial native load.
    if not errors:
        for receipt in plan['source_receipts']:
            if not receipt.get('path'):
                continue
            path = Path(receipt['path']).resolve()
            def remove_cache(path=path, receipt=receipt):
                _require(path.name == receipt['owned_filename'], 'The owned Hair Source cache path changed.')
                _require(not any(str(Path(bpy.path.abspath(library.filepath)).resolve()) == str(path)
                                 for library in bpy.data.libraries),
                         'A Source Library still uses the new Hair cache; it was kept for recovery.')
                path.unlink(missing_ok=True)
            attempt(remove_cache)
    if errors:
        raise RuntimeError('Hair Source/checkpoint cleanup needs review: ' + '; '.join(errors))
    plan['created_actions'].clear()
    plan['source_receipts'].clear()


def commit(plan, new_names, new_action=None, new_slot=None):
    """Commit only proven selected Hair delta and a caller-owned local Action."""
    _require(isinstance(plan, dict) and plan.get('kind') == 'hair_replace.authority_plan',
             'An eligibility summary cannot commit Hair authority; prepare a fresh transaction.')
    _require(not plan.get('committed') and not plan.get('written'), 'This Hair authority plan was already committed.')
    new_names = _names(new_names)
    rig, api, revision = plan['rig'], plan['api'], plan['revision']
    full_rest = _verify_delta(plan, new_names)
    if not plan['managed']:
        plan['committed'] = True
        return dict(managed=False, item_id=None)
    _history_unchanged(plan)
    # Supported writers touch only newly owned Actions. Old Action identity,
    # slot, metadata and checkpoints are checked again after publication;
    # unrelated historical clip curves are not scanned by this Hair operation.
    _require([revision._row(item) for item in plan['saved'].items] == plan['rows'],
             'The worklist changed after Hair replacement was prepared.')
    action = new_action if new_action is not None else plan.get('replacement_action')
    handle = new_slot if new_slot is not None else plan.get('replacement_slot')
    _require(isinstance(action, bpy.types.Action) and action not in plan['before_actions']
             and action.library is None and not action.asset_data,
             'Hair replacement needs a newly owned local Custom Action.')
    slot = api.worklist._slot(action, handle)
    _require(rig.animation_data is not None and rig.animation_data.action is action
             and rig.animation_data.action_slot_handle == slot.handle,
             'Bind the prepared replacement Custom before committing Hair authority.')
    plan['replacement_action'], plan['replacement_slot'] = action, slot.handle
    # These keys may be inherited only by the new caller-owned Action.
    for key in (*api.target._action_motion_keys(), revision.TOKEN, revision.CHECKPOINT):
        action.pop(key, None)
    revision._no_action_ID_properties(dict(action.items()))
    token, item_id, work_id = (uuid.uuid4().hex for _ in range(3))
    try:
        # This is a fresh baseline for the new topology, not permission to run
        # the old Source on missing Hair bones. Keep all old rows in checkpoint.
        temporary = action.copy()
        plan['created_actions'].append(temporary)
        temporary.use_fake_user = False
        revision._no_action_ID_properties(dict(temporary.items()))
        revision._no_native_dependencies(temporary)
        receipt = {}
        plan['source_receipts'].append(receipt)
        try:
            source, source_path, source_sha = api.worklist._linked_baseline(
                plan['saved'], temporary, item_id, plan['selected_row']['name'] + ' · Hair',
                _ownership=receipt)
        finally:
            # The utility admits only an initially empty ownership receipt.
            # This filename is ours even if its native writer/load raised.
            receipt['owned_filename'] = item_id + '.blend'
        _require(source not in plan['before_actions'] and source.library is not None
                 and source.library not in plan['before_libraries'],
                 'The new Hair Source unexpectedly reused a pre-existing native ID.')
        plan['created_actions'].append(source)
        source_slot = api.worklist._slot(source, slot.handle)
        # Native Action.copy and .blend serialization create this independent
        # baseline. Attest the new Source once; the unchanged current-revision
        # validator checks its saved fingerprint again on the installed row.
        curve_hash = revision._action_hash(source, source_slot.handle, api)
        revision._remove_action(temporary)
        plan['written'] = True
        adoption = _modules()[3]
        audit = json.loads(plan['audit_raw'])
        audit['accepted_native_rest_sha256'] = api.body._hash(adoption._native_rest(rig))
        audit_raw = adoption._canonical(audit)
        proof = json.loads(plan['owner_raw'])
        proof.update(rest=full_rest,
            # Match complete164 Body's own _finish_owner_revision schema;
            # do not depend on helpers present only in unrelated candidates.
            bone_properties={bone.name: api.body._hash(api.body._bone_ownership(bone))
                             for bone in rig.data.bones},
            audit_sha256=api.body._hash(audit_raw))
        owner_raw = adoption._canonical(proof)
        rig.data[adoption.AUDIT_KEY] = audit_raw
        rig.data[api.body.RECOVERY_OWNER_KEY] = owner_raw
        prior = plan['prior']
        backups = copy.deepcopy(prior[0]['backups']) if prior else []
        refs = dict(prior[1]) if prior else {}
        backups.append(dict(removed=sorted(plan['old_names'] - new_names),
                            snapshot=plan['backup_description']))
        refs['backup_' + str(len(backups) - 1)] = plan['backup']
        # A checkpoint is installed before ownership callbacks consume the
        # passive historical-bone bridge. No older checkpoint is modified.
        checkpoint = bpy.data.actions.new('Current Hair Work Checkpoint')
        plan['created_actions'].append(checkpoint)
        plan['checkpoint'] = checkpoint
        _PENDING[checkpoint.as_pointer()] = plan
        checkpoint.use_fake_user = True
        refs.update(rig=rig, checkpoint=checkpoint)
        value = dict(schema=SCHEMA, history_sha256=api.body._hash(plan['history_raw']),
            history_refs_sha256=api.body._hash(plan['history_refs']), current_rest=full_rest,
            current_structure=api.pose._structure(rig), owner_raw=owner_raw, audit_raw=audit_raw,
            data_history_key=next(key for key, data in plan['history_refs'].items() if data is rig.data),
            backups=backups, item_id=item_id, checkpoint_sha256='')
        # Bootstrap only within this synchronous commit: historical_bone needs
        # no worklist/adoption callback and the record is completed below.
        preliminary = dict(schema=revision.SCHEMA, current_rest=full_rest,
                           current_structure=value['current_structure'])
        revision._store(checkpoint, preliminary, {})
        value['checkpoint_sha256'] = revision._digest(preliminary)
        rig[REFS] = refs
        rig[RECORD] = revision._json(dict(payload=value, sha256=revision._digest(value)))
        _require(adoption.recovery_owners(rig) == plan['owners']
                 and adoption.verified_revision(rig),
                 'The selected Hair revision changed historical owners or Body authority.')
        capture = api.target._capture(plan['context'], rig, include_owned_ik_poles=True)
        api.target._validate_capture(plan['context'], capture)
        revision._validate_paths(rig, action, slot.handle, api, capture)
        row = dict(plan['selected_row'])
        row.update(item_id=item_id, work_item_id=work_id, clip_key='local-current:' + item_id,
            current_rig_revision=token, custom_action=action, custom_slot=slot.handle, side='CUSTOM',
            source_action=source, source_slot=source_slot.handle, source_file=source_path,
            source_hash=source_sha,
            manifest_path='', new_animation=False, new_action_id='',
            link_identity=revision._json(dict(schema=revision.SCHEMA, token=token, local_only=True,
                historical_item_id=plan['selected_id'], hair_topology_revision=True)))
        for key in ('source_baseline_receipt', 'last_synced_receipt', 'scan_reason', 'retirement_reason',
                    'composition_json', 'composition_sha256', 'application_role'):
            if key in row:
                row[key] = ''
        for key in ('sync_selected', 'b_edited', 'composition_changed', 'effective_inputs_changed', 'export_needed'):
            if key in row:
                row[key] = False
        for key in ('source_data', 'custom_data'):
            if key in row:
                row[key] = None
        if 'scan_state' in row:
            row['scan_state'] = 'NOT_SCANNED'
        payload = dict(schema=revision.SCHEMA, revision_id=uuid.uuid4().hex, rig=rig,
            scene=plan['context'].scene, view_layer=plan['context'].view_layer.name,
            history_raw=plan['history_raw'], history_refs=plan['history_refs'], historical_owners=plan['owners'],
            capture=capture, current_rest=full_rest, current_structure=value['current_structure'],
            old_rows=plan['rows'], previous_index=plan['previous_index'],
            source_curve_hashes={item_id: curve_hash}, rows=[row],
            semantic_scope='selected Hair topology; fresh read-only Source matches new Custom; historical drafts retained')
        checkpoint_refs = {}
        encoded = revision._encode(payload, checkpoint_refs, plan['history_refs'], rig)
        revision._store(checkpoint, encoded, checkpoint_refs)
        value['checkpoint_sha256'] = revision._digest(encoded)
        rig[RECORD] = revision._json(dict(payload=value, sha256=revision._digest(value)))
        action[revision.TOKEN], action[revision.CHECKPOINT] = token, checkpoint
        # Keep other rows visible and untouched; strict old revision readers
        # refuse their stale full-Rest captures instead of dropping curves.
        rows = [row if old['item_id'] == plan['selected_id'] else old for old in plan['rows']]
        revision._replace_rows(plan['saved'], rows)
        plan['saved'].active_index = next(i for i, item in enumerate(plan['saved'].items)
                                         if item.item_id == item_id)
        plan['saved'].status, plan['saved'].has_error = 'Selected Hair replaced on the current rig.', False
        revision.validate(plan['context'], item_id)
        _history_unchanged(plan)
        _verify_actions(plan)
        plan['committed'], plan['item_id'] = True, item_id
        return dict(managed=True, item_id=item_id, action=action, checkpoint=checkpoint,
                    source_action=source, source_file=source_path, source_hash=source_sha)
    except Exception:
        _undo_metadata(plan)
        raise
    finally:
        checkpoint = plan.get('checkpoint')
        if checkpoint is not None:
            _PENDING.pop(checkpoint.as_pointer(), None)


def rollback(plan):
    """Undo owned authority after the caller restores native bones/Action."""
    _require(isinstance(plan, dict) and plan.get('kind') == 'hair_replace.authority_plan',
             'An eligibility summary owns no Hair authority transaction to roll back.')
    api, rig, revision = plan['api'], plan['rig'], plan['revision']
    _require(rig.data is plan['data'] and api.ua._rest_state(rig.data) == plan['old_rest']
             and _graph(rig, plan['old_names'], api) == plan['graph'],
             'Restore the original native Hair delta before rolling back its authority.')
    if plan.get('managed'):
        _undo_metadata(plan)
        _history_unchanged(plan)
        _verify_actions(plan)
    _cleanup_created_actions(plan)
    for backup in reversed(plan['created_armatures']):
        _require(backup.users - int(backup.use_fake_user) == 0,
                 'A passive Hair backup gained a foreign user; it was kept for recovery.')
        backup.use_fake_user = False
        bpy.data.armatures.remove(backup, do_unlink=False)
    plan['created_armatures'].clear()
    plan['committed'] = False
