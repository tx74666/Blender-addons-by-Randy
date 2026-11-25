"""Permission for a local wrist edit, never a restore/export capability.

No Source contents, files, Action points or other scenes are read. The current
native object, editable Custom binding, row and optional checkpoint references
authorize only the target rotations that this operation can write. Historical
restoration and publication retain their separate complete validators.
"""
from types import SimpleNamespace
import uuid


def _require(value, message):
    if not value:
        raise ValueError(message)


def _modules():
    import bpy
    from . import animation_worklist as worklist, animation_pose_test as pose
    from . import body_original_mode as original, animation_rest_pose as rest
    from . import animation_composition as composition, unity_animation as ua
    from . import animation_worklist_revision as revision, forearm_twist as forearm
    return SimpleNamespace(bpy=bpy, worklist=worklist, pose=pose, original=original,
        rest=rest, composition=composition, ua=ua, revision=revision, forearm=forearm)


def _context(context, rig, api):
    _require(context.object is rig and context.mode in {'OBJECT', 'POSE'}
        and rig.type == 'ARMATURE' and not rig.library and not rig.data.library
        and not rig.override_library and not rig.data.override_library
        and rig.is_editable and rig.data.is_editable,
        'Choose the local editable wrist rig in Object or Pose Mode.')
    _require(context.scene.objects.get(rig.name) is rig
        and context.view_layer.objects.get(rig.name) is rig,
        'Return to this wrist rig\'s scene and view layer.')
    _require(not getattr(getattr(context, 'screen', None), 'is_animation_playing', False),
        'Pause playback before resetting the wrist.')
    api.worklist._idle(context)
    _require(not api.original.active(rig) and not api.pose.active(rig)
        and not api.pose.natural_active(rig) and not api.rest.is_rest(rig),
        'Finish Original, Base Pose or Rest editing before resetting the IK wrist.')
    _require(not api.composition.active(rig) and api.ua.active_preview(rig) is None
        and 'character_designer_rigging_session_v1' not in rig,
        'Finish the unrelated animation preview before resetting the wrist.')
    preview = getattr(api.forearm, '_SESSION', None)
    _require(not preview or preview.get('armature') is not rig,
        'Finish Forearm calibration before resetting the wrist.')
    if context.scene.tool_settings.use_keyframe_insert_auto:
        _require(all(getattr(getattr(context.scene, name, None), 'active', None) is None
            for name in ('keying_sets', 'keying_sets_all')),
            'Use the default keying policy before resetting wrist rotation with Auto Key.')


def _bag(action, slot):
    _require(action.is_action_layered and len(action.layers) == 1
        and len(action.layers[0].strips) == 1,
        'This wrist edit needs one unambiguous Action layer and strip.')
    layer, strip = action.layers[0], action.layers[0].strips[0]
    _require(strip.type == 'KEYFRAME' and getattr(layer, 'influence', 1.) == 1.
        and not getattr(layer, 'mute', False), 'The active wrist Action layer is unsupported.')
    bags = [bag for bag in strip.channelbags if bag.slot_handle == slot]
    _require(len(bags) == 1, 'The current Custom slot has no unique keyframe channelbag.')
    return bags[0]


def _revision_owner(api, rig, row, action, source):
    revision = api.revision
    token = getattr(row, 'current_rig_revision', '')
    checkpoint = None
    if token:
        _require(type(token) is str and uuid.UUID(hex=token).hex == token
            and action.get(revision.TOKEN) == token,
            'The current Custom revision token changed.')
        checkpoint = action.get(revision.CHECKPOINT)
        _require(isinstance(checkpoint, api.bpy.types.Action) and checkpoint is not action
            and not checkpoint.library and checkpoint.use_fake_user
            and revision.RECORD in checkpoint,
            'The current Custom has no retained local checkpoint owner.')
        refs = checkpoint.get(revision.REFS)
        _require(refs is not None and hasattr(refs, 'values'), 'The checkpoint references are missing.')
        values = tuple(refs.values())
        _require(any(value is rig for value in values) and any(value is action for value in values)
            and any(value is source for value in values),
            'The current Custom, Source and rig are not owned by this checkpoint.')
    else:
        _require(not any(key in action for key in (revision.TOKEN, revision.CHECKPOINT)),
            'This Custom lost its current revision row identity.')
    return token, checkpoint


def _known_users(saved, rig, action, checkpoint, api):
    # Count known native references, not arbitrary owners discovered by a
    # whole-file user_map. A foreign animation user makes in-place keying refuse.
    count = int(action.use_fake_user) + int(bool(rig.animation_data and rig.animation_data.action is action))
    count += int(rig.get(api.worklist.links.ACTION_KEY) is action)
    for rows in (saved.items, getattr(saved, 'retired_items', ())):
        for row in rows:
            count += int(getattr(row, 'custom_action', None) is action)
            count += int(getattr(row, 'source_action', None) is action)
    if checkpoint is not None:
        count += sum(value is action for value in checkpoint[api.revision.REFS].values())
    return count


def _unkeyed_local_slot(context, rig, action, saved, api):
    """Admit a current-frame pose edit on a native Action outside the worklist."""
    from . import animation_action_browser as browser
    from . import animation_worklist_target as target
    slot = rig.animation_data.action_slot
    _require(api.pose._pose_owner(rig) and action.is_editable and not action.library
        and not action.override_library and not action.asset_data and slot is not None
        and slot.id_data == action and slot.target_id_type in {'OBJECT', 'UNSPECIFIED'},
        'Choose a local working Action and its Object slot to reset this wrist.')
    _require(not browser._foreign_hint(action, rig)
        and action.get(browser.ROLE_KEY) in (None, 'WORK')
        and action.get(target.ACTION_RIG_KEY, rig) == rig
        and not any(obj.get(api.worklist.links.ACTION_KEY) == action
                    for obj in api.bpy.data.objects if obj.type == 'ARMATURE')
        and not any(key in action for key in (api.revision.RECORD, api.revision.REFS,
                                               api.pose.SESSION_KEY, api.pose.REFS_KEY))
        and api.worklist._rigging_module(rig, action) is None,
        'Switch from the protected animation to a local working Action before resetting the wrist.')
    # An old revision can become the artist's explicitly classified daily
    # Action. Its checkpoint is history, never authority to restore or export.
    if any(key in action for key in (api.revision.TOKEN, api.revision.CHECKPOINT,
                                     target.ACTION_MOTION_KEY, target.ACTION_RIG_KEY)):
        _require(action.get(browser.ROLE_KEY) == 'WORK' and browser._owned_hint(action, rig),
            'Choose the local working Action; this managed animation has no active worklist row.')
    _require(not any(row.rig == rig or row.source_action == action or row.custom_action == action
                     for row in getattr(saved, 'retired_items', ())),
        'Use the managed worklist to edit a retired rig or Action.')
    for scene in api.bpy.data.scenes:
        if scene == context.scene:
            continue
        worklist = scene.character_designer_animation_worklist
        _require(not any(action in (row.source_action, row.custom_action)
                         for rows in (worklist.items, worklist.retired_items) for row in rows),
                 'This Action belongs to an animation worklist in another scene.')
    return int(slot.handle)


def _local_key_owner(rig, action, slot, api):
    if action.users == 1 + int(action.use_fake_user):
        return
    for user in api.bpy.data.user_map(subset={action}).get(action, ()):
        animation = getattr(user, 'animation_data', None)
        if not animation or user == rig:
            continue
        if animation.action == action and animation.action_slot_handle == slot:
            raise ValueError('Another animation owner uses this Action slot.')
        strips = [strip for track in animation.nla_tracks for strip in track.strips]
        while strips:
            strip = strips.pop()
            if getattr(strip, 'action', None) == action and strip.action_slot_handle == slot:
                raise ValueError('Another animation owner uses this Action slot in NLA.')
            strips.extend(getattr(strip, 'strips', ()))


def prepare(context, rig):
    api = _modules()
    _context(context, rig, api)
    ad = rig.animation_data
    action = ad.action if ad else None
    owner = dict(context=context, rig=rig, data=rig.data, api=api, action=action,
        frame=float(context.scene.frame_current + context.scene.frame_subframe),
        autokey=bool(context.scene.tool_settings.use_keyframe_insert_auto),
        row=None, item_id=None, slot=None, bag=None, source=None, source_slot=None,
        checkpoint=None, token='', expected_action_users=0)
    if action is None:
        _require(not owner['autokey'], 'Choose an editable Custom Action before using Auto Key.')
        return owner
    _require(not ad.use_tweak_mode and not (ad.use_nla and any(not track.mute and track.strips for track in ad.nla_tracks))
        and ad.action_blend_type == 'REPLACE' and ad.action_influence == 1.,
        'Finish NLA or weighted Action editing before resetting the wrist.')
    saved, row = api.worklist.state(context), api.worklist.current(context)
    if row is None and not saved.items:
        slot = _unkeyed_local_slot(context, rig, action, saved, api)
        owner.update(slot=slot, unkeyed_local=not owner['autokey'], local_action=True,
                     workspace_path=saved.workspace_path)
        if owner['autokey']:
            _local_key_owner(rig, action, slot, api)
            owner.update(bag=_bag(action, slot), expected_action_users=action.users)
        return owner
    _require(row is not None and row.rig is rig and row.side == 'CUSTOM'
        and row.custom_action is action and not action.library and not action.asset_data
        and action.is_editable and ad.action_slot is not None
        and ad.action_slot.handle == row.custom_slot,
        'Select this rig\'s editable Custom Action and slot; Source remains read-only.')
    _require(not getattr(row, 'new_animation', False) and not getattr(row, 'composition_json', '')
        and api.worklist._rigging_module(rig, action) is None
        and not any(key in action for key in (api.pose.SESSION_KEY, api.pose.REFS_KEY)),
        'This draft uses its own editing workflow.')
    source = row.source_action
    _require(source is not None and source is not action and source.library is not None,
        'Keep the independent linked Source identity for this Custom.')
    token, checkpoint = _revision_owner(api, rig, row, action, source)
    owner.update(row=row, item_id=row.item_id, slot=int(ad.action_slot.handle),
        bag=_bag(action, ad.action_slot.handle), source=source, source_slot=row.source_slot,
        source_library=source.library, checkpoint=checkpoint, token=token,
        expected_action_users=(_known_users(saved, rig, action, checkpoint, api) if owner['autokey'] else 0))
    return owner


def validate(context, owner):
    _require(owner['context'] is context, 'Apply the wrist edit in its original context.')
    _require(owner['autokey'] == bool(context.scene.tool_settings.use_keyframe_insert_auto),
             'The wrist Auto Key policy changed.')
    fresh = prepare(context, owner['rig'])
    fields = ('rig', 'data', 'action', 'frame', 'autokey', 'item_id', 'slot',
              'source', 'source_slot', 'checkpoint', 'token', 'expected_action_users')
    _require(all(fresh[name] == owner[name] for name in fields),
        'The prepared wrist frame, keying policy or Custom owner changed.')
    _require(fresh.get('unkeyed_local') == owner.get('unkeyed_local')
             and fresh.get('workspace_path') == owner.get('workspace_path'),
             'The current wrist Action changed editing workflows.')
    if owner.get('local_action') and owner['autokey']:
        _require(fresh.get('local_action') and fresh['bag'] == owner['bag'],
                 'The local wrist Action or channelbag changed.')
    elif owner['action'] is not None and not owner.get('unkeyed_local'):
        _require(fresh['bag'] == owner['bag'] and fresh['source_library'] is owner['source_library'],
            'The current Custom channelbag or Source identity changed.')
    return owner


def resolve(context, rig, target, owner):
    from . import limb_ik_wrist_rotation
    return limb_ik_wrist_rotation._resolve_local(context, rig, target, owner)
