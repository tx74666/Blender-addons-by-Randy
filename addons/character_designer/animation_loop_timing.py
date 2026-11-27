"""Loop timing shared by imported motion, controller preparation and playback."""
import math


LOOP = 'character_designer_prepared_loop'


def is_loop(action):
    """Use the prepared Action's explicit loop flag or its Unity import flag."""
    get = getattr(action, 'get', None)
    if get is None:
        return False
    flag = get(LOOP, get('unity_loop_time', False))
    return type(flag) in (bool, int) and flag == 1


def round_period(duration):
    """Nearest whole-frame loop period, including half-up ties and very short clips."""
    return max(1, math.floor(duration + 0.5))


def frame_scale(source_duration_frames, source_fps, target_fps, align_loop):
    """Output frames per source frame; all interior keys retain their relative phase."""
    if align_loop and source_duration_frames > 0.:
        duration = source_duration_frames * target_fps / source_fps
        return round_period(duration) / source_duration_frames
    return target_fps / source_fps
