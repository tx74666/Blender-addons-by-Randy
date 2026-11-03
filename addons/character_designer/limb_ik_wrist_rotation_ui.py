"""Conditional Alt+R routing for owned IK hands; native clear stays available.

Polling identifies event ownership only. Permissions and neutral matching belong
to the service, whose refusals must consume the event rather than clear natively.
"""

import bpy
from bpy.types import Operator


_KEYMAPS = []


def _limb():
    from . import limb_ik
    return limb_ik


def _backend():
    from . import limb_ik_wrist_rotation
    return limb_ik_wrist_rotation


def _owned_wrist(pose_bone):
    """Cheap tags only; the service authenticates the complete live graph."""
    try:
        armature = pose_bone.id_data
        if armature.type != 'ARMATURE':
            return False
        limb = _limb()
        owner = armature.data.get(limb.ARMATURE_ID_KEY)
        return bool(owner and limb._owned(pose_bone.bone, owner, role='HAND_IK'))
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False


class CHARACTERDESIGNER_OT_limb_ik_clear_wrist_rotation(Operator):
    bl_idname = 'character_designer.limb_ik_clear_wrist_rotation'
    bl_label = 'Reset IK Wrist Rotation'
    bl_description = 'Reset generated IK wrists to their own neutral FK orientations; Pose Mirror includes both hands'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if getattr(context, 'mode', None) != 'POSE':
            return False
        try:
            return any(_owned_wrist(pb) for pb in (context.selected_pose_bones or ()))
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False

    def execute(self, context):
        try:
            selected = tuple(context.selected_pose_bones or ())
            if (getattr(context, 'mode', None) != 'POSE' or len(selected) not in (1, 2)
                    or any(not _owned_wrist(pb) or pb.id_data != getattr(context, 'object', None) for pb in selected)):
                self.report({'WARNING'}, 'Select only one or both generated IK hand controls; nothing was changed.')
                return {'CANCELLED'}
            if len(selected) == 1:
                _backend().reset_wrist_neutral(context, target=selected[0])
            else:
                _backend().reset_wrist_neutral(context, targets=selected)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Reset wrist rotation to neutral.')
        return {'FINISHED'}


CLASSES = (CHARACTERDESIGNER_OT_limb_ik_clear_wrist_rotation,)


def _registration_state():
    return tuple(cls for cls in CLASSES if cls.is_registered), tuple(_KEYMAPS)


def _remove_owned_keymaps(pairs):
    for km, item in reversed(pairs):
        try:
            if any(existing == item for existing in km.keymap_items):
                km.keymap_items.remove(item)
        except ReferenceError:
            pass  # Its owning native keymap was already removed.
        _KEYMAPS.remove((km, item))


def _rollback_registration(before):
    classes, pairs = before
    _remove_owned_keymaps([pair for pair in _KEYMAPS if pair not in pairs])
    for cls in reversed(CLASSES):
        if cls not in classes and cls.is_registered:
            bpy.utils.unregister_class(cls)


def register():
    before = _registration_state()
    try:
        for cls in CLASSES:
            if not cls.is_registered:
                bpy.utils.register_class(cls)
        manager = getattr(bpy.context, 'window_manager', None)
        config = getattr(getattr(manager, 'keyconfigs', None), 'addon', None)
        if config is not None and not _KEYMAPS:
            km = config.keymaps.get('Pose')
            if km is None:
                km = config.keymaps.new(name='Pose', space_type='EMPTY', region_type='WINDOW')
            if km.space_type != 'EMPTY' or km.region_type != 'WINDOW':
                raise RuntimeError('The Pose keymap has an unexpected editor context.')
            item = km.keymap_items.new(CLASSES[0].bl_idname, 'R', 'PRESS',
                alt=True, shift=False, ctrl=False, oskey=False, any=False, head=True)
            _KEYMAPS.append((km, item))
    except Exception:
        _rollback_registration(before)
        raise


def unregister():
    _remove_owned_keymaps(list(_KEYMAPS))
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
