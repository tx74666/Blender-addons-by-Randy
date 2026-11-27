"""Compose local upper-body Actions using the same native NLA primitives as Composition."""

from contextlib import contextmanager
import math
import uuid

import bpy

from . import animation_composition as composition
from . import animation_weapon_profiles as weapons
from . import animation_weapon_grip as grip
from .animation_loop_timing import is_loop

SESSION = 'character_designer_local_composition_v1'
DRAFT = 'character_designer_local_upper_body'
TRANSFORMS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')


def session(rig):
    return rig.get(SESSION) if rig is not None else None


def upper_body_paths(rig):
    from . import limb_ik, limb_ik_fk, torso_controls

    left, right = (weapons.arm_chain(rig, side) for side in ('L', 'R'))
    if left is None or right is None:
        raise ValueError('Composition needs two identifiable arm chains on this rig.')
    bones = rig.data.bones
    candidates = set()
    for chain in (left, right):
        arm = bones[chain.upper]
        branch = arm.parent if arm.parent and limb_ik._SHOULDER_TOKENS.intersection(
            limb_ik._name_parts(arm.parent.name)[0]) else arm
        candidates.update((branch, *branch.children_recursive))
    left_ancestors = tuple(bones[left.upper].parent_recursive)
    right_ancestors = set(bones[right.upper].parent_recursive)
    chest = next((bone for bone in left_ancestors if bone in right_ancestors), None)
    if chest and chest.parent is not None:
        # Include the chest and head branch only when the shared ancestor is
        # anatomically above both hips. A shared root/hips never enters B.
        branch = {chest, *chest.children_recursive}
        legs = [choices[0] for side in ('L', 'R')
                if (choices := limb_ik._candidate_chains(rig, 'LEG', side))]
        if (len(legs) == 2 and all(bones[leg.upper] not in branch for leg in legs)
                and any(token in {'chest', 'spine', 'upperchest', 'torso'}
                        for token in limb_ik._name_parts(chest.name)[0])):
            candidates.update(branch)
    inputs = {bone.name for bone in candidates
              if bone.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS}
    inventory = limb_ik._validate_inventory(rig, limb_pose_only=True)
    target_names = set()
    for side in ('L', 'R'):
        record = inventory['rigs'].get(('ARM', side))
        if record:
            target_names.add(record['target'].name)
            inputs.update((record['target'].name, record['pole'].name))
    torso = torso_controls.validate(rig, inventory=inventory, pose_only=True)
    if torso and set(torso['sources']).issubset(inputs):
        inputs.add(torso['bend'])
    paths = {rig.pose.bones[name].path_from_id(prop) for name in inputs for prop in TRANSFORMS}
    paths.update(rig.pose.bones[name].path_from_id() + '["' + limb_ik_fk.PROPERTY + '"]'
                 for name in target_names)
    if grip.WEIGHT in rig:
        paths.add(grip.PATH)
    return paths


def _unique_slot(action, handle):
    choices = [slot for slot in action.slots if slot.target_id_type == 'OBJECT'
               and (not handle or slot.handle == handle)]
    if len(choices) != 1:
        raise ValueError('Choose the exact Object slot for the upper-body Action.')
    composition._bag(action, choices[0].handle)
    return choices[0].handle


def _clock(scene, action):
    from .unity_animation import native_action_frame_rate

    return (native_action_frame_rate(action) or (scene.render.fps / scene.render.fps_base))


def _track(ad, action, handle, start, end, placement, speed, label):
    # Base and Overlay keep their own native clocks in the existing NLA helper.
    title = label.encode('utf-8')[:48].decode('utf-8', errors='ignore')
    name, number = title, 2
    while ad.nla_tracks.get(name) is not None:
        name = f'{title} · {number:02d}'
        number += 1
    track = composition._track(ad, name, action, handle, start, end, scene_start=placement, speed=speed)
    strip = track.strips[0]
    strip.frame_end = placement + (end - start) / speed
    return track


def _entry(track):
    strip = track.strips[0]
    return {'action': strip.action, 'slot': strip.action_slot_handle, 'name': track.name}


def tracks_for(rig, *, exact=False):
    saved = session(rig)
    ad = rig.animation_data if rig is not None else None
    if saved is None or ad is None:
        raise ValueError('Open a local upper-body Composition first.')
    tracks = []
    for row in (saved['base'], *saved['overlays'].values()):
        track = ad.nla_tracks.get(row['name'])
        if (track is None or len(track.strips) != 1 or track.strips[0].action != row['action']
                or track.strips[0].action_slot_handle != row['slot']):
            raise ValueError('A Composition track changed identity. Recover it in the NLA Editor.')
        tracks.append(track)
    if exact:
        if (ad.action is not None or not ad.use_nla or any(track.is_solo for track in ad.nla_tracks)
                or [t for t in ad.nla_tracks if t.strips and not t.mute] != tracks
                or any(track.strips[0].mute for track in tracks)):
            raise ValueError('Restore the Composition Base and Overlay tracks before exporting.')
        paths = upper_body_paths(rig)
        for track in tracks[1:]:
            strip = track.strips[0]
            if strip.blend_type != 'REPLACE' or not all(
                    curve.data_path in paths for curve in composition._curves(strip.action, strip.action_slot_handle)):
                raise ValueError('A Composition overlay now contains channels outside the upper body.')
    return tracks


def add_overlay(context, rig, action, *, handle=0, frame, blend_in=0, blend_out=0,
                hold=False, grip_weight=None, loop_base=None):
    weapons.require_idle(context, rig)
    if rig is None or action is None or action.library or action.get(DRAFT):
        raise ValueError('Choose a local source Action for the upper-body overlay.')
    ad = rig.animation_data
    saved = session(rig)
    if saved is not None and saved['scene'] != context.scene:
        raise ValueError('Return to the Composition Scene before changing its tracks.')
    base_strip = None
    previous_repeat = None
    if saved is not None:
        base_strip = tracks_for(rig, exact=True)[0].strips[0]
        previous_repeat = base_strip.repeat
    elif (not ad or not ad.action or ad.action_influence != 1 or ad.action_blend_type != 'REPLACE'
          or any(track.strips and not track.mute for track in ad.nla_tracks)
          or any(track.is_solo for track in ad.nla_tracks)):
        raise ValueError('Start from a single directly playing Base Action.')
    base = saved['base']['action'] if saved is not None else ad.action
    if base == action:
        raise ValueError('Choose a separate Action from the locomotion Base.')
    handle = _unique_slot(action, handle)
    base_handle = saved['base']['slot'] if saved is not None else _unique_slot(base, ad.action_slot_handle)
    paths = upper_body_paths(rig)
    if grip_weight is not None and (not math.isfinite(grip_weight) or not 0 <= grip_weight <= 1):
        raise ValueError('Choose a Left Grip weight between zero and one.')
    if not any(curve.data_path in paths and not curve.mute for curve in composition._curves(action, handle)):
        raise ValueError('This Action has no supported upper-body channels in the selected slot.')
    start, end = map(float, action.frame_range)
    astart, aend = map(float, base.frame_range)
    scene = context.scene
    scene_fps = scene.render.fps / scene.render.fps_base
    speed = _clock(scene, action) / scene_fps
    if (not all(math.isfinite(value) for value in (frame, blend_in, blend_out, speed))
            or frame < -1048574 or frame > 1048574 or min(blend_in, blend_out) < 0
            or end <= start or aend <= astart or blend_in + blend_out > (end - start) / speed
            or hold and blend_out):
        raise ValueError('Choose a positive Action range and fades that fit inside the overlay. A hold has no fade out.')
    owner = str(uuid.uuid4())
    old_use_nla, old_tracks = ad.use_nla, set(ad.nla_tracks)
    old_session = None if saved is None else {
        'scene': saved['scene'], 'base': dict(saved['base']), 'use_nla': saved['use_nla'],
        'loop_base': bool(saved.get('loop_base', False)),
        'overlays': {key: dict(row) for key, row in saved['overlays'].items()},
    }
    keep_source = action.users == 0
    draft = action.copy()
    try:
        composition._host('animation_worklist')._clean_action(draft)
        draft[DRAFT] = owner
        draft.name = action.name + ' · Upper Body'
        overrides = grip.overlay_values(rig, grip_weight) if grip_weight is not None else {}
        paths.difference_update(overrides)
        for layer in draft.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in list(bag.fcurves):
                        if bag.slot_handle != handle or curve.data_path not in paths:
                            bag.fcurves.remove(curve)
        for path, value in overrides.items():
            composition._constant_curve(composition._bag(draft, handle), path, 0, value, start, end)
        draft.use_frame_range = True
        draft.frame_start, draft.frame_end = start, end
        if saved is None:
            # The currently playing Action uses one Action frame per Scene frame.
            track = _track(ad, base, base_handle, astart, aend, astart, 1, 'Composition · Base')
            base_strip = track.strips[0]
            base_strip.extrapolation = ad.action_extrapolation
            ad.action, ad.use_nla = None, True
            saved_values = {'scene': scene, 'base': _entry(track), 'overlays': {}, 'use_nla': old_use_nla,
                            'loop_base': is_loop(base) if loop_base is None else bool(loop_base)}
        else:
            saved_values = {**old_session, 'overlays': dict(old_session['overlays'])}
        overlay = _track(ad, draft, handle, start, end, frame, speed, 'Composition · ' + action.name)
        strip = overlay.strips[0]
        if saved_values['loop_base'] and strip.frame_end > base_strip.frame_end:
            repeats = math.ceil((strip.frame_end - base_strip.frame_start) /
                                (base_strip.action_frame_end - base_strip.action_frame_start))
            if repeats > 1000:
                raise ValueError('The requested overlay extends beyond the native Base repeat limit.')
            base_strip.repeat = repeats
        strip.extrapolation = 'HOLD_FORWARD' if hold else 'NOTHING'
        strip.blend_in, strip.blend_out = blend_in, blend_out
        saved_values['overlays'][owner] = _entry(overlay)
        rig[SESSION] = saved_values
        scene.frame_set(scene.frame_current, subframe=scene.frame_subframe)
        if keep_source:
            action.use_fake_user = True
    except Exception:
        for track in tuple(ad.nla_tracks):
            if track not in old_tracks:
                ad.nla_tracks.remove(track)
        if saved is None:
            ad.action, ad.action_slot, ad.use_nla = base, composition._slot(base, base_handle), old_use_nla
            rig.pop(SESSION, None)
        else:
            if base_strip.repeat != previous_repeat:
                base_strip.repeat = previous_repeat
            rig[SESSION] = old_session
        bpy.data.actions.remove(draft)
        raise
    return draft


@contextmanager
def snapshot_metadata(rig):
    """Keep Scene authoring references out of the skeletal export snapshot."""
    records = [rig[SESSION]]
    attachment = weapons.attachment(rig)
    if attachment is not None:
        records.append(attachment)
    scenes = [(record, record['scene']) for record in records]
    try:
        for record, _scene in scenes:
            del record['scene']
        yield
    finally:
        for record, scene in scenes:
            record['scene'] = scene


def close(context, rig):
    weapons.require_idle(context, rig)
    saved = session(rig)
    if saved is None or saved['scene'] != context.scene or rig.animation_data.action is not None:
        raise ValueError('Return to the saved Composition without an active Action before closing it.')
    tracks = tracks_for(rig)
    ad = rig.animation_data
    base, handle = saved['base']['action'], saved['base']['slot']
    slot, use_nla = composition._slot(base, handle), bool(saved['use_nla'])
    for track in tracks[1:]:
        track.strips[0].action.use_fake_user = True
    for track in reversed(tracks):
        ad.nla_tracks.remove(track)
    ad.action, ad.action_slot, ad.use_nla = base, slot, use_nla
    del rig[SESSION]
    context.scene.frame_set(context.scene.frame_current, subframe=context.scene.frame_subframe)
