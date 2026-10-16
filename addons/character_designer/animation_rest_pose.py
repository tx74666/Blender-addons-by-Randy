"""Native Rest/Pose display without replacing artist Actions or rest data.

The persistent marker contains a data name, never an extra ID reference in the
authenticated Main Rig recovery graph. Only this module's matching Rest preview
is resumed automatically by an ordinary worklist operation.
"""

from contextlib import contextmanager

import bpy


PREVIEW_KEY = 'character_designer_animation_rest_display_v1'


def is_rest(rig):
    """Read native display state, including Rest selected outside this tool."""
    try:
        return rig is not None and rig.type == 'ARMATURE' and rig.data.pose_position == 'REST'
    except (AttributeError, ReferenceError):
        return False


def _marker(data):
    return 'v1:' + data.name_full


def get_rig(context):
    """Resolve one local Main Rig, protecting another worklist's rig and data."""
    scene = context.scene
    main = getattr(getattr(scene, 'character_designer_setup', None), 'rig', None)
    work = getattr(getattr(scene, 'character_designer_animation_worklist', None), 'rig', None)
    if main is not None and work is not None and main is not work:
        raise ValueError('The Main Rig and animation worklist belong to different rigs; their displays were kept.')
    rig = main if main is not None else work
    if rig is None or rig.type != 'ARMATURE':
        raise ValueError('Choose this character\'s Main Rig before displaying Rest Pose.')
    data = rig.data
    if (rig.library or data.library or rig.override_library or getattr(data, 'override_library', None)
            or not rig.is_editable or not data.is_editable):
        raise ValueError('Rest Pose display needs one locally editable Main Rig.')
    if (scene.objects.get(rig.name) is not rig
            or context.view_layer.objects.get(rig.name) is not rig):
        raise ValueError('Return to the Main Rig scene and view layer before changing its display.')
    active = getattr(context, 'object', None)
    if (rig.mode == 'EDIT' or active is not None
            and active.type == 'ARMATURE' and active.mode == 'EDIT'):
        raise ValueError('Finish Armature Edit Mode before changing Rest/Pose display.')
    if any(obj is not rig and obj.type == 'ARMATURE' and obj.data is data
           for obj in bpy.data.objects):
        raise ValueError('Another Armature object shares this rig data; Rest/Pose display left both untouched.')
    return rig


def can_show(context):
    """UI admission; mesh Edit Mode is allowed and is never exited here."""
    try:
        get_rig(context)
        return True
    except (ValueError, AttributeError, ReferenceError):
        return False


def _set_display(context, rig, position):
    rig.data.pose_position = position
    context.view_layer.update()


def _show(context, position):
    from . import unity_animation as ua
    rig = get_rig(context)
    data, previous = rig.data, rig.data.pose_position
    had_marker, previous_marker = PREVIEW_KEY in rig, rig.get(PREVIEW_KEY)
    ua._set_playing(context, False)
    try:
        _set_display(context, rig, position)
        if position == 'REST':
            rig[PREVIEW_KEY] = _marker(data)
        else:
            rig.pop(PREVIEW_KEY, None)
        return rig
    except Exception:
        data.pose_position = previous
        if had_marker:
            rig[PREVIEW_KEY] = previous_marker
        else:
            rig.pop(PREVIEW_KEY, None)
        context.view_layer.update()
        raise


def show_rest(context):
    """Display native Rest without clearing pose channels or authored keys."""
    return _show(context, 'REST')


def show_pose(context):
    """Display the retained native pose/animation, preserving mesh Edit Mode."""
    return _show(context, 'POSE')


@contextmanager
def resume_for_operation(context, rig):
    """Resume an owned Rest display; a failed worklist operation stays in Rest.

    Native Rest chosen elsewhere remains under the existing worklist admission
    rules. The caller's existing transaction owns Action/data/rigging rollback.
    This scope owns display rollback only and never changes the recovery schema.
    A successful backend may restore another retained Armature data ID; that
    data's original display is respected. No native work runs after it commits.
    """
    marker = rig.get(PREVIEW_KEY) if rig is not None and hasattr(rig, 'get') else None
    if marker is None:
        yield
        return
    data = rig.data
    if marker != _marker(data):
        raise ValueError('The Rest display rig data changed; use Pose Position explicitly before continuing.')
    if get_rig(context) is not rig:
        raise ValueError('The Rest display belongs to another Main Rig.')
    previous = data.pose_position
    try:
        _set_display(context, rig, 'POSE')
        yield
    except Exception:
        data.pose_position = previous
        rig[PREVIEW_KEY] = marker
        context.view_layer.update()
        raise
    else:
        rig.pop(PREVIEW_KEY, None)
