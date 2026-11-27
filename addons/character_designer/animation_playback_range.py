"""Choose an inclusive playback range and retain artist preview ranges per prepared clip."""
import math

from .animation_loop_timing import LOOP, is_loop, round_period


_OWNER = 'character_designer_preview_range_clip'
_APPLIED = 'character_designer_preview_range_applied'
_CUSTOM = 'character_designer_preview_range_custom'


def bounds(start, end, loop):
    first = math.floor(start)
    last = first + round_period(end - start) - 1 if loop else math.ceil(end)
    return first, max(first, last)


def set_ranges(scene, start, end, loop, custom=None):
    scene.frame_start = math.floor(start)
    scene.frame_end = max(scene.frame_start, math.ceil(end))
    first, last = custom or bounds(start, end, loop)
    scene.frame_preview_start = first
    scene.frame_preview_end = last
    scene.use_preview_range = True
    return scene.frame_preview_start, scene.frame_preview_end


def record_loop(record):
    action = record.controller_action
    if LOOP in action:
        return is_loop(action)
    return is_loop(action) or bool(record.source_action and is_loop(record.source_action))


def active_record(scene):
    settings = scene.character_designer_animation_worklist
    rig = scene.character_designer_setup.rig
    ad = rig.animation_data if rig else None
    if ad is None or ad.action is None:
        return None
    key = settings.get(_OWNER)
    binding = ad.action, ad.action_slot_handle
    matched = None
    for record in scene.character_designer_controller_motions:
        if (record.target == rig and record.workspace_identity == settings.workspace_identity
                and binding in ((record.controller_action, record.controller_slot),
                                (record.alternate_action, record.alternate_slot))):
            if key:
                if record.clip_key == key:
                    return record
            elif matched is not None:
                return None
            else:
                matched = record
    return matched


def custom_range(scene, record, *, active=True):
    if record is None:
        return None
    if active and scene.use_preview_range:
        current = scene.frame_preview_start, scene.frame_preview_end
        if _APPLIED in record:
            if current != tuple(record[_APPLIED]):
                return current
        else:
            # Earlier releases wrote the inclusive Action range to both scene
            # and preview. Keep an artist's narrower range on that same binding.
            action = scene.character_designer_setup.rig.animation_data.action
            start, end = action.frame_range
            legacy = math.floor(start), math.ceil(end)
            if legacy == (scene.frame_start, scene.frame_end) and current != legacy:
                return current
    return tuple(record[_CUSTOM]) if _CUSTOM in record else None


def remember(scene, outgoing, custom, record, applied):
    if outgoing is not None and custom is not None:
        outgoing[_CUSTOM] = list(custom)
    record[_APPLIED] = list(applied)
    scene.character_designer_animation_worklist[_OWNER] = record.clip_key


def reset(scene, record):
    start, end = record.controller_action.frame_range
    applied = set_ranges(scene, start, end, record_loop(record))
    if _CUSTOM in record:
        del record[_CUSTOM]
    record[_APPLIED] = list(applied)
