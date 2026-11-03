"""Transactional worklist ownership for native keyed IK/FK Actions.

Source and existing rows/Actions/checkpoints remain immutable. A single staged
copy is keyed in place and published as a new Custom row. Explicit orphan
recovery copies the current local Action without rebinding its historical parent.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
import uuid

RECORD = 'character_designer_ik_fk_action_owner_v1'
VERSION = 1


def _require(value, message):
    if not value:
        raise ValueError(message)


def _modules():
    from . import control_pose_offset as offset
    return offset, offset._modules()


def _clone(value):
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clone(item) for item in value]
    return value


def _capture(api, context, rig):
    # This explicit capability must be shipped with the owner service. It is
    # never simulated by deleting Pole curves or rewriting an old checkpoint.
    try:
        result = api.target._capture(context, rig, include_owned_ik_poles=True)
    except TypeError as exc:
        raise ValueError('Current IK/FK ownership needs verified owned-Pole recovery support.') from exc
    api.target._validate_capture(context, result)
    return result


def _checkpoint_state(api, action):
    checkpoint = action.get(api.revision.CHECKPOINT)
    if checkpoint is None:
        return None
    _require(isinstance(checkpoint, api.bpy.types.Action), 'The parent checkpoint reference changed.')
    return checkpoint, checkpoint.get(api.revision.RECORD), dict(checkpoint[api.revision.REFS])


def capture_display(rig):
    """Fresh RAM authority for this operation, never a historical permission."""
    from . import bone_collections as groups
    key = rig.as_pointer()
    return dict(rig=rig, key=key, data=rig.data, data_pointer=rig.data.as_pointer(),
        collections=tuple((collection, collection.as_pointer(), collection.name,
            collection.parent, collection.parent.as_pointer() if collection.parent else None,
            tuple(collection.bones), tuple(bone.as_pointer() for bone in collection.bones))
            for collection in rig.data.collections_all),
        caches={name: (getattr(groups, name), key in getattr(groups, name), getattr(groups, name).get(key))
                for name in ('_FRAME_CACHE', '_FRAME_OWNER_REJECTIONS')})


def restore_display(saved):
    """Restore only original member order and this rig's two RAM cache entries."""
    from . import bone_collections as groups
    rig, key = saved['rig'], saved['key']

    def validate():
        _require(rig.as_pointer() == key and rig.data is saved['data']
            and rig.data.as_pointer() == saved['data_pointer']
            and all(getattr(groups, name) is table for name, (table, _present, _value) in saved['caches'].items()),
            'IK/FK display recovery ownership changed; retain the recovery data.')
        current = tuple(rig.data.collections_all)
        _require(tuple(collection.as_pointer() for collection in current)
            == tuple(row[1] for row in saved['collections']), 'IK/FK recovery collections changed.')
        for collection, row in zip(current, saved['collections']):
            parent = collection.parent
            _require(collection == row[0] and collection.name == row[2] and parent == row[3]
                and (parent.as_pointer() if parent else None) == row[4]
                and tuple(bone.as_pointer() for bone in row[5]) == row[6],
                'IK/FK recovery collection or original bone identity changed.')

    validate()
    changed = []
    for row in saved['collections']:
        members = tuple(row[0].bones)
        pointers = tuple(bone.as_pointer() for bone in members)
        _require(len(pointers) == len(row[6]) and set(pointers) == set(row[6]),
            'IK/FK recovery did not restore the exact member set; retain the recovery data.')
        if pointers != row[6]:
            changed.append((row, members))
    for row, members in changed:
        for bone in members:
            row[0].unassign(bone)
        for bone in row[5]:
            row[0].assign(bone)
    validate()
    _require(all(tuple(bone.as_pointer() for bone in row[0].bones) == row[6] for row in saved['collections']),
        'IK/FK recovery could not restore native member order.')
    for table, present, value in saved['caches'].values():
        if present:
            table[key] = value
        else:
            table.pop(key, None)


def _parent_unchanged(plan):
    api = plan['api']
    item = api.worklist._item(plan['saved'], plan['item_id'])
    _require(api.revision._row(item) == plan['parent_row'], 'The historical Custom row changed during IK/FK keying.')
    _require(all(plan['offset']._action_hash(api, plan['parent'], handle) == digest
                 for handle, digest in plan['parent_hashes'].items()), 'The parent Custom changed during IK/FK keying.')
    _require(plan['offset']._action_hash(api, plan['source'], plan['source_slot']) == plan['source_hash'],
             'The read-only Source changed during IK/FK keying.')
    saved = plan['parent_checkpoint_state']
    if saved is not None:
        checkpoint, record, refs = saved
        _require(checkpoint.get(api.revision.RECORD) == record and dict(checkpoint[api.revision.REFS]) == refs,
                 'The fixed historical checkpoint or its exact references changed.')
    return item


def _plan(offset, api, context, rig, saved, item, original, slot, value, label, *, recovery=False, variant=None):
    _require(type(label) is str and label.strip() and len(label) <= 120, 'Choose a short IK/FK version label.')
    capture = _capture(api, context, rig)
    parent = item.custom_action
    hashes = {s.handle: offset._action_hash(api, original, s.handle) for s in original.slots if s.target_id_type == 'OBJECT'}
    parent_hashes = {s.handle: offset._action_hash(api, parent, s.handle) for s in parent.slots if s.target_id_type == 'OBJECT'}
    return dict(owner_api=VERSION, phase='prepared', recovery=recovery, offset=offset, api=api,
        context=context, rig=rig, data=rig.data, saved=saved, item_id=item.item_id,
        parent=parent, original=original, slot=int(slot.handle), original_slot=slot,
        parent_row=api.revision._row(item), parent_hashes=parent_hashes,
        parent_checkpoint_state=_checkpoint_state(api, parent),
        source=item.source_action, source_slot=item.source_slot,
        source_hash=offset._action_hash(api, item.source_action, item.source_slot), hashes=hashes,
        revision_value=value, capture=capture, frame=float(context.scene.frame_current + context.scene.frame_subframe),
        label=label.strip(), previous=(saved.active_index, saved.status, saved.has_error),
        previous_target=context.window_manager.character_designer_animation.target,
        variant=variant, staged_action=None, stage_checkpoint=None,
        owned_row_id=None, owned_row_index=None, owned_checkpoint=None,
        display_attempt=capture_display(rig))


def _managed(api, context, rig):
    saved = api.worklist.state(context)
    ad = rig.animation_data
    action = ad.action if ad else None
    rows = tuple(saved.items) + tuple(getattr(saved, 'retired_items', ()))
    related = any(getattr(row, 'rig', None) is rig for row in rows)
    related = related or api.target.managed(rig)
    related = related or rig.get(api.worklist.links.ACTION_KEY) is not None
    related = related or 'character_designer_rigging_session_v1' in rig
    if action is not None:
        _require(not action.library and not action.asset_data, 'Source and Pose assets are read-only for IK/FK keying.')
        _require(not any(getattr(row, 'source_action', None) is action for row in rows),
                 'Source remains read-only for IK/FK keying.')
        related = related or any(key in action for key in (api.revision.TOKEN, api.revision.CHECKPOINT,
            api.pose.SESSION_KEY, api.pose.REFS_KEY,
            'character_designer_rigging_capture_v1', 'character_designer_rigging_input_action_v1'))
        related = related or any(getattr(row, 'custom_action', None) is action for row in rows)
    return bool(related)


def prepare(context, rig, *, label='IK/FK Switch'):
    """Read-only admission before matching; None means explicitly unmanaged."""
    offset, api = _modules()
    _require(rig is not None and rig.type == 'ARMATURE' and not rig.library and not rig.data.library
        and not rig.override_library and rig.is_editable and rig.data.is_editable
        and context.view_layer.objects.active is rig and context.mode in {'OBJECT', 'POSE'},
        'Choose the current local editable IK/FK armature first.')
    _require(not api.pose.natural_active(rig), 'Finish the Natural Pose workspace before keyed IK/FK ownership changes.')
    if not _managed(api, context, rig):
        return None
    variant = offset.prepare_variant(context, rig, label=label)
    item = api.worklist._item(variant['saved'], variant['item_id'])
    result = _plan(offset, api, context, rig, variant['saved'], item, variant['original'],
        api.worklist._slot(variant['original'], variant['slot']), variant['revision_value'], label, variant=variant)
    # The156 variant admission is reused, but recovery coverage belongs only to
    # this new transaction and includes the formally supported owned Pole state.
    variant['capture'] = result['capture']
    variant.pop('item', None)
    return result


def _owner(plan, *, final=False):
    _require(isinstance(plan, dict) and plan.get('owner_api') == VERSION
        and plan.get('phase') in {'prepared', 'staged'}, 'This IK/FK owner transaction is no longer active.')
    api, context, rig = plan['api'], plan['context'], plan['rig']
    _require(rig.data is plan['data'] and context.view_layer.objects.active is rig
        and context.mode in {'OBJECT', 'POSE'}, 'The IK/FK rig or context changed.')
    _require(float(context.scene.frame_current + context.scene.frame_subframe) == plan['frame'],
             'The frame changed during IK/FK keying.')
    item = _parent_unchanged(plan)
    current = api.worklist.current(context)
    _require(current is not None and current.item_id == plan['item_id'], 'Another worklist row became active.')
    ad = rig.animation_data
    _require(ad is not None and ad.action in (plan['original'], plan['staged_action'])
        and ad.action_slot is not None and ad.action_slot.handle == plan['slot'],
        'The active IK/FK Action or slot changed.')
    if plan['variant'] is not None:
        plan['offset']._variant_owner(plan['variant'], final=final)
    elif final:
        api.revision.validate(context, plan['item_id'])
    if final:
        api.target._validate_capture(context, plan['capture'])
        _require(all(plan['offset']._action_hash(api, plan['original'], handle) == digest
                     for handle, digest in plan['hashes'].items()), 'The retained input Action changed.')
    return item


def stage(plan):
    """Create and bind one complete copied Action; subsequent keys are in place."""
    _owner(plan)
    if plan['staged_action'] is not None:
        return plan['staged_action']
    if not plan['recovery']:
        # Matching is complete, but binding/keying the copied Action can
        # schedule evaluation of unrelated existing curves. Keep this fresh
        # input witness separate from the prepare capture used by rollback.
        plan['pending_capture'] = _capture(plan['api'], plan['context'], plan['rig'])
    if plan['variant'] is not None:
        try:
            action = plan['offset'].new_variant_action(plan['variant'])
        finally:
            # Copy/hash failures after allocation still belong to this plan.
            plan['staged_action'] = plan['variant'].get('staged_action')
    else:
        action = plan['original'].copy()
        plan['staged_action'] = action
        action.name = plan['original'].name + ' · ' + plan['label']
        action.use_fake_user = True
        _require(all(plan['offset']._action_hash(plan['api'], action, handle) == digest
                     for handle, digest in plan['hashes'].items()), 'The recovery copy changed complete Action content.')
    plan['staged_action'], plan['phase'] = action, 'staged'
    plan['stage_checkpoint'] = action.get(plan['api'].revision.CHECKPOINT)
    ad = plan['rig'].animation_data
    ad.action = action
    ad.action_slot = plan['api'].worklist._slot(action, plan['slot'])
    return action


def _keyed_input_paths(plan, switches):
    """Exact raw components written by this call's native key service."""
    from . import limb_ik_fk as match
    rig, api = plan['rig'], plan['api']
    inventory = match._limb()._validate_inventory(rig)
    targets = {data['target'].name: data for data in inventory['rigs'].values()}
    owned_poles = api.target._owned_ik_pole_constraints(rig)
    pose, properties, angles = set(), set(), set()
    _require(isinstance(switches, (list, tuple)) and switches,
             'Identify the native IK/FK inputs keyed by this transaction.')
    for target, affected, pole_angles in switches:
        _require(target.name in targets and rig.pose.bones.get(target.name) == target,
                 'The keyed IK/FK target is not an owned native limb.')
        _require(isinstance(affected, (set, frozenset, list, tuple))
            and all(type(name) is str and rig.pose.bones.get(name) is not None for name in affected),
            'The keyed IK/FK transform inputs changed.')
        properties.add((target.name, match.PROPERTY))
        for name in affected:
            pb = rig.pose.bones[name]
            rotation = ('rotation_quaternion' if pb.rotation_mode == 'QUATERNION'
                        else 'rotation_axis_angle' if pb.rotation_mode == 'AXIS_ANGLE'
                        else 'rotation_euler')
            for field in ('location', rotation, 'scale'):
                pose.update((name, field, index) for index in range(len(getattr(pb, field))))
        selected_poles = {con.as_pointer() for _pb, con, record in targets[target.name]['entries']
                          if record['role'] == 'IK'}
        for constraint, _previous in pole_angles:
            paths = [(name, key) for (name, key), con in owned_poles.items() if con == constraint]
            _require(len(paths) == 1 and constraint.as_pointer() in selected_poles,
                     'The keyed Pole input is not this limb\'s authenticated native IK angle.')
            angles.add(paths[0])
    return pose, properties, angles


def preserve_staged(plan, switches):
    """Retain pending inputs across copied-key evaluation before route updates."""
    _owner(plan)
    api, context, rig = plan['api'], plan['context'], plan['rig']
    _require(plan['phase'] == 'staged' and not plan['recovery']
        and rig.animation_data.action is plan['staged_action']
        and 'pending_capture' in plan, 'The staged IK/FK input witness is unavailable.')
    pending = plan['pending_capture']
    api.target._validate_capture(context, pending)
    execution = _capture(api, context, rig)
    display = capture_display(rig)
    keyed_pose, keyed_properties, keyed_angles = _keyed_input_paths(plan, switches)
    previous = {entry['target'].as_pointer(): entry for entry in pending['targets']}
    _require(set(previous) == {entry['target'].as_pointer() for entry in execution['targets']},
             'The staged IK/FK recovery targets changed.')
    for entry in execution['targets']:
        target = entry['target']
        old = previous[target.as_pointer()]
        _require(entry['data'] is old['data']
            and set(entry['snapshot']['pose']) == set(old['snapshot']['pose'])
            and (target is rig or entry['action'] is old['action']
                 and entry['snapshot']['slot'] == old['snapshot']['slot']),
            'The staged IK/FK target data or secondary Action changed.')
        primary = target is rig
        for name, saved in old['snapshot']['pose'].items():
            current = entry['snapshot']['pose'][name]
            current['rotation_mode'] = saved['rotation_mode']
            for field, values in saved.items():
                if field != 'rotation_mode':
                    current[field] = [value if not primary or (name, field, index) not in keyed_pose
                                      else current[field][index] for index, value in enumerate(values)]
            _require(set(entry['properties'][name]) == set(old['properties'][name]),
                     'The staged IK/FK numeric input inventory changed.')
            for key, value in old['properties'][name].items():
                if not primary or (name, key) not in keyed_properties:
                    entry['properties'][name][key] = value
            # Constraint influence/mutes and display remain the fresh matched
            # state. Only explicitly captured owned Pole scalar inputs belong
            # to this pending-input merge.
            for key, values in old['constraints'][name].items():
                if 'pole_angle' in values and (not primary or (name, key) not in keyed_angles):
                    entry['constraints'][name][key]['pole_angle'] = values['pole_angle']
    api.target._validate_capture(context, execution)
    _preserve_published_pose(plan, execution, display)


def _publish_checkpoint(plan, action, row):
    """Own a new checkpoint only; tests may fault after this function returns."""
    api, revision = plan['api'], plan['api'].revision
    if plan['revision_value'] is None:
        return None
    value = _clone(plan['revision_value'])
    token = uuid.uuid4().hex
    row.update(current_rig_revision=token, work_item_id=uuid.uuid4().hex,
        clip_key='local-current:' + row['item_id'],
        link_identity=revision._json(dict(schema=revision.SCHEMA, token=token,
            local_only=True, historical_item_id=plan['item_id'])))
    value['capture'] = _capture(api, plan['context'], plan['rig'])
    value['rows'].append(row)
    value['source_curve_hashes'][row['item_id']] = value['source_curve_hashes'][plan['item_id']]
    value['ik_fk_parent_item_id'] = plan['item_id']
    value['ik_fk_parent_checkpoint'] = plan['parent_checkpoint_state'][0]
    if plan['recovery']:
        value['ik_fk_recovered_action'] = plan['original']
    checkpoint = api.bpy.data.actions.new('IK FK Work Checkpoint')
    plan['owned_checkpoint'] = checkpoint
    references = {}
    encoded = revision._encode(value, references, dict(plan['rig'][api.target.REFS]), plan['rig'], custom=action, token=token)
    # A $custom payload remains cycle-safe for decode, while this exact typed
    # reference independently proves current ownership to wrist keying readers.
    references['ik_fk_current_action'] = action
    revision._store(checkpoint, encoded, references)
    action[revision.TOKEN], action[revision.CHECKPOINT] = token, checkpoint
    item = api.worklist._item(plan['saved'], plan['item_id'])
    probe = SimpleNamespace(**row)
    probe.bl_rna = item.bl_rna
    revision._read(plan['context'], probe, revision._modules())
    return checkpoint


def _append_row(plan, row):
    """Never retain a parent RNA row across CollectionProperty.add()."""
    plan['owned_row_index'] = len(plan['saved'].items)
    item = plan['saved'].items.add()
    plan['owned_row_id'] = row['item_id']
    item.item_id = row['item_id']
    for key, value in row.items():
        setattr(item, key, value)
    return row['item_id']


def publish(plan):
    """Publish exactly one new editable row with a validated independent owner."""
    _owner(plan, final=True)
    _require(plan['phase'] == 'staged' and plan['staged_action'] is not None, 'Stage the IK/FK Action before publication.')
    api, action, rig = plan['api'], plan['staged_action'], plan['rig']
    _require(rig.animation_data.action is action and not action.library and not action.asset_data
        and action.users - int(action.use_fake_user) - 1 == 0, 'The staged Action gained an unrelated user; retain it for recovery.')
    # Binding a copied Action schedules reevaluation of its existing keys.
    # Preserve the matched/unkeyed overrides before publishing its metadata.
    published_pose = _capture(api, plan['context'], rig)
    published_display = capture_display(rig)
    row = _clone(plan['parent_row'])
    new_id = uuid.uuid4().hex
    row.update(item_id=new_id, name=row['name'] + ' · ' + plan['label'],
        custom_action=action, custom_slot=plan['slot'], side='CUSTOM')
    if 'sync_selected' in row:
        row['sync_selected'] = False
    action.use_fake_user = True
    action[RECORD] = json.dumps(dict(version=VERSION, parent_item_id=plan['item_id'],
        frame=plan['frame'], label=plan['label'], recovered=plan['recovery']), ensure_ascii=False)
    checkpoint = _publish_checkpoint(plan, action, row)
    _append_row(plan, row)
    item = api.worklist._item(plan['saved'], new_id)
    if checkpoint is not None:
        api.revision.validate(plan['context'], new_id)
    else:
        api.worklist._check_baseline(item)
        api.worklist._association(item)
    _parent_unchanged(plan)
    plan['saved'].active_index = next(i for i, item in enumerate(plan['saved'].items) if item.item_id == new_id)
    _require(float(plan['context'].scene.frame_current + plan['context'].scene.frame_subframe) == plan['frame'],
             'IK/FK publication changed the current frame.')
    _preserve_published_pose(plan, published_pose, published_display)
    plan['phase'] = 'published'
    plan['saved'].status, plan['saved'].has_error = 'IK/FK keyed in a new editable Custom version.', False
    if plan['variant'] is not None:
        plan['variant']['phase'] = 'published'
    return dict(action=action, item_id=new_id, checkpoint=checkpoint, frame=plan['frame'])


def _preserve_published_pose(plan, captured, display):
    """Flush copied keys, then retain this call's raw overrides without keying."""
    api, context, rig = plan['api'], plan['context'], plan['rig']
    _require(rig.animation_data.action is plan['staged_action'] and rig.data is plan['data'],
             'The published IK/FK Action or data changed before preserving the pose.')
    execution = _clone(captured)
    # Ordinary Custom publication intentionally updates its Link association.
    # Only its fresh post-publication metadata may be replayed here.
    execution['metadata'] = _capture(api, context, rig)['metadata']
    api.revision._restore_current(context, rig, execution, api.revision._modules())
    restore_display(display)


def rollback(plan):
    """Restore the fresh transaction capture, then clear only newly owned IDs."""
    if plan is None:
        return
    _require(isinstance(plan, dict) and plan.get('owner_api') == VERSION, 'Invalid IK/FK owner recovery plan.')
    if plan['phase'] == 'rolled_back':
        return
    api, rig, context = plan['api'], plan['rig'], plan['context']
    _require(rig.data is plan['data'] and context.view_layer.objects.active is rig
        and context.mode in {'OBJECT', 'POSE'}
        and float(context.scene.frame_current + context.scene.frame_subframe) == plan['frame'],
        'The IK/FK recovery context or frame changed; retain the recovery IDs.')
    current = api.worklist.current(context)
    _require(current is not None and current.item_id in {plan['item_id'], plan.get('owned_row_id')},
             'Another worklist row became active; retain the IK/FK recovery IDs.')
    _require(rig.animation_data is not None
        and rig.animation_data.action in (plan['original'], plan['staged_action']),
        'Another Action became active; retain the IK/FK recovery IDs.')
    _parent_unchanged(plan)
    if plan['staged_action'] is None:
        api.revision._restore_current(plan['context'], plan['rig'], plan['capture'], api.revision._modules())
        restore_display(plan['display_attempt'])
        plan['context'].window_manager.character_designer_animation.target = plan['previous_target']
        plan['saved'].active_index, plan['saved'].status, plan['saved'].has_error = plan['previous']
        _parent_unchanged(plan)
        plan['phase'] = 'rolled_back'
        return
    api, action = plan['api'], plan['staged_action']
    checkpoint = plan.get('owned_checkpoint')
    refs = dict(checkpoint.get(api.revision.REFS, {})) if checkpoint is not None else {}
    known = int(action.use_fake_user) + int(plan['rig'].animation_data.action is action)
    known += int(plan['rig'].get(api.worklist.links.ACTION_KEY) is action)
    known += sum(item.item_id == plan.get('owned_row_id') and item.custom_action is action for item in plan['saved'].items)
    known += sum(value is action for value in refs.values())
    _require(action.users == known, 'The staged IK/FK Action gained a foreign user; recovery IDs were retained.')
    if checkpoint is not None:
        _require(checkpoint.users == int(checkpoint.use_fake_user) + int(action.get(api.revision.CHECKPOINT) is checkpoint),
                 'The new IK/FK checkpoint gained a foreign user; recovery IDs were retained.')
    api.revision._restore_current(plan['context'], plan['rig'], plan['capture'], api.revision._modules())
    plan['context'].window_manager.character_designer_animation.target = plan['previous_target']
    row_id = plan.get('owned_row_id')
    removed_row = False
    if row_id:
        index = next((i for i, item in enumerate(plan['saved'].items) if item.item_id == row_id), None)
        if index is not None:
            item = plan['saved'].items[index]
            _require(item.custom_action is None or item.custom_action is action, 'A foreign row replaced the owned IK/FK publication.')
            plan['saved'].items.remove(index)
            removed_row = True
    if not removed_row and plan.get('owned_row_index') is not None:
        index = plan['owned_row_index']
        if len(plan['saved'].items) == index + 1:
            item = plan['saved'].items[index]
            _require(not item.item_id and item.custom_action is None, 'An incomplete row acquired a foreign identity.')
            plan['saved'].items.remove(index)
    plan['saved'].active_index, plan['saved'].status, plan['saved'].has_error = plan['previous']
    if checkpoint is not None:
        current_checkpoint = action.get(api.revision.CHECKPOINT)
        _require(current_checkpoint in (checkpoint, plan['stage_checkpoint']), 'The new IK/FK checkpoint owner changed.')
        if current_checkpoint is checkpoint:
            action.pop(api.revision.CHECKPOINT, None)
        checkpoint.pop(api.revision.REFS, None)
        checkpoint.use_fake_user = False
        if checkpoint.users == 0:
            api.bpy.data.actions.remove(checkpoint)
    action.use_fake_user = False
    if action.users == 0:
        api.bpy.data.actions.remove(action)
    _parent_unchanged(plan)
    restore_display(plan['display_attempt'])
    plan['phase'] = 'rolled_back'


def prepare_recovery(context, rig, *, parent_item_id=None, label='Recovered IK/FK'):
    """Explicitly admit an isolated inherited clone, without altering its owner."""
    offset, api = _modules()
    api.worklist._idle(context)
    _require(rig is not None and rig.type == 'ARMATURE' and not rig.library and not rig.data.library
        and not rig.override_library and rig.is_editable and rig.data.is_editable
        and context.mode in {'OBJECT', 'POSE'} and api.pose._pose_owner(rig), 'Choose the local editable animation rig.')
    _require(context.scene.objects.get(rig.name) is rig and context.view_layer.objects.get(rig.name) is rig
        and not getattr(getattr(context, 'screen', None), 'is_animation_playing', False), 'Stop playback in this rig scene before recovery.')
    _require(not api.pose.active(rig) and not api.pose.natural_active(rig)
        and not api.original.active(rig) and not api.rest.is_rest(rig)
        and not api.composition.active(rig) and not api.ua.active_preview(rig), 'Finish the unrelated pose/animation workspace before recovery.')
    _require('character_designer_rigging_session_v1' not in rig, 'Captured Rigging input has its own recovery workflow.')
    saved, item = api.worklist.state(context), api.worklist.current(context)
    _require(item is not None and (parent_item_id is None or item.item_id == parent_item_id)
        and item.rig is rig and item.side == 'CUSTOM', 'Choose the exact parent Custom row before recovery.')
    _require(not getattr(item, 'new_animation', False) and not getattr(item, 'composition_json', '')
        and bool(getattr(item, 'current_rig_revision', '')), 'Only an existing current-Rig Custom revision can own this recovery.')
    ad = rig.animation_data
    action, parent = ad.action if ad else None, item.custom_action
    _require(action is not None and action is not parent and parent is not None
        and not action.library and not action.asset_data and action.is_editable
        and not parent.library and not parent.asset_data and ad.action_slot is not None
        and not ad.use_tweak_mode and not (ad.use_nla and any(not track.mute and track.strips for track in ad.nla_tracks))
        and ad.action_blend_type == 'REPLACE' and ad.action_influence == 1., 'The active clone or animation binding is not isolated.')
    _require(api.worklist._rigging_module(rig, action) is None
        and not any(key in action for key in (api.pose.SESSION_KEY, api.pose.REFS_KEY)), 'This draft has a separate recovery owner.')
    value = api.revision.validate(context, item.item_id)
    token, checkpoint = parent.get(api.revision.TOKEN), parent.get(api.revision.CHECKPOINT)
    _require(action.get(api.revision.TOKEN) == token == item.current_rig_revision
        and action.get(api.revision.CHECKPOINT) is checkpoint, 'The active clone has no exact inherited parent checkpoint.')
    rows = tuple(saved.items) + tuple(getattr(saved, 'retired_items', ()))
    _require(not any(getattr(row, 'custom_action', None) is action or getattr(row, 'source_action', None) is action for row in rows),
             'The active Action already has a row owner; no orphan was claimed.')
    parents = [row for row in saved.items if row.rig is rig and row.custom_action is parent
        and getattr(row, 'current_rig_revision', '') == token]
    _require(len(parents) == 1, 'The inherited parent row is ambiguous.')
    expected_users = int(action.use_fake_user) + 1 + int(rig.get(api.worklist.links.ACTION_KEY) is action)
    _require(action.users == expected_users, 'The active clone has a foreign native user; retain it.')
    _require(not any(value is action for value in checkpoint[api.revision.REFS].values()), 'The active clone already has a checkpoint reference owner.')
    api.revision._no_action_ID_properties({key: value for key, value in action.items()
        if key not in {api.revision.TOKEN, api.revision.CHECKPOINT}})
    slot = api.worklist._slot(action, item.custom_slot)
    _require(ad.action_slot.handle == slot.handle, 'The orphan Action slot changed.')
    capture = _capture(api, context, rig)
    api.revision._validate_paths(rig, action, slot.handle, api.revision._modules(), capture)
    return _plan(offset, api, context, rig, saved, item, action, slot, value, label, recovery=True)


def publish_recovery(plan):
    _require(plan.get('recovery') is True, 'Use explicit orphan recovery admission first.')
    stage(plan)
    return publish(plan)
