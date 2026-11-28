"""Edit-pose wrist rotation for owned IK hands and explicit chain resets."""

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from . import limb_ik_wrist_move


_KEYMAPS = []


def _limb():
    from . import limb_ik
    return limb_ik


def _backend():
    from . import limb_ik_wrist_rotation
    return limb_ik_wrist_rotation


def _selected_feet(context):
    from . import limb_ik_ui_state
    if not context.object.data.get(limb_ik_ui_state._FOOT_RECORD):
        return ()
    from . import limb_ik_foot_rotation
    return limb_ik_foot_rotation.selected_feet(context)


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
    bl_description = 'Restore managed wrists or foot controls in the chosen reference space'
    bl_options = {'REGISTER', 'UNDO'}

    reference: EnumProperty(
        name='Rotation Reference',
        items=(('ARMATURE', 'Edit Pose', ''), ('FOREARM', 'Posed Limb', 'Follow the current forearm, shin or calibrated toe space')),
        default='ARMATURE', options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if getattr(context, 'mode', None) != 'POSE':
            return False
        try:
            selected = context.selected_pose_bones or ()
            rig = context.object
            if any(obj != rig for obj in getattr(context, 'objects_in_mode', ())):
                return False
            wrists = [pb for pb in selected if _owned_wrist(pb)]
            if not wrists:
                feet = _selected_feet(context)
                if not feet:
                    return False
                if len(feet) != len(selected):
                    from . import selected_limb_rotation
                    if not selected_limb_rotation.supports(context, wrists, feet):
                        return False
            if (any(pb.id_data != rig for pb in selected)
                    or any(any(pb.lock_rotation)
                           or (pb.rotation_mode in {'QUATERNION', 'AXIS_ANGLE'} and pb.lock_rotations_4d and pb.lock_rotation_w)
                           or float(pb.get('ik_fk', 1.)) <= 1e-6 for pb in wrists)):
                return False
            if context.scene.tool_settings.use_keyframe_insert_auto:
                settings = context.scene.tool_settings
                if (context.scene.keying_sets.active is not None
                        or settings.use_keyframe_insert_keyingset or settings.use_keyframe_cycle_aware
                        or settings.auto_keying_mode != 'ADD_REPLACE_KEYS'
                        or context.preferences.edit.use_visual_keying or not rig.animation_data
                        or not rig.animation_data.action):
                    return False
            return True
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False

    def execute(self, context):
        try:
            selected = tuple(context.selected_pose_bones or ())
            wrists = tuple(pb for pb in selected if _owned_wrist(pb))
            if (getattr(context, 'mode', None) != 'POSE'
                    or any(pb.id_data != getattr(context, 'object', None) for pb in selected)):
                self.report({'WARNING'}, 'Choose one or both generated IK wrists and controls on the same rig.')
                return {'CANCELLED'}
            if len(wrists) == 2 and len(selected) >= 9:
                from . import natural_pose_clear
                if (natural_pose_clear.body_selected(context, allow_auto_key=True)
                        and natural_pose_clear._visible_selected(context, {p.name for p in selected})):
                    natural_pose_clear.clear_body_rotation(context, wrists)
                    self.report({'INFO'}, 'Cleared body rotations against the original bind pose.')
                    return {'FINISHED'}
            feet = _selected_feet(context) if len(selected) != len(wrists) else ()
            if feet:
                from . import limb_ik_foot_rotation, selected_limb_rotation
                if selected_limb_rotation.supports(context, wrists, feet):
                    selected_limb_rotation.reset(context, wrists, feet, follow_shin=self.reference == 'FOREARM')
                    self.report({'INFO'}, 'Cleared selected limb rotations.')
                    return {'FINISHED'}
                if len(feet) == len(selected):
                    limb_ik_foot_rotation.reset_rotation(context, feet, follow_shin=self.reference == 'FOREARM')
                    self.report({'INFO'}, 'Cleared selected foot rotations.')
                    return {'FINISHED'}
            if len(wrists) not in (1, 2):
                return {'CANCELLED'}
            if len(selected) != len(wrists):
                companions = tuple(pb for pb in selected if pb not in wrists)
                from . import controller_transform_clear
                result = controller_transform_clear.reset_mixed_rotation(
                    context, wrists, companions, follow_forearm=self.reference == 'FOREARM')
            elif len(wrists) == 1:
                result = _backend().reset_wrist_neutral(context, target=wrists[0], follow_forearm=self.reference == 'FOREARM')
            else:
                result = _backend().reset_wrist_neutral(context, targets=wrists, follow_forearm=self.reference == 'FOREARM')
            if context.scene.tool_settings.use_keyframe_insert_auto and result['keyed']:
                from . import natural_bind_mode
                names = result.get('targets') or (result['target'],)
                natural_bind_mode.note_rotation_keys(context, context.object, names)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Reset selected rotations.' if len(selected) != len(wrists)
                    else 'Reset wrist rotation to neutral.')
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_clear_body_transform_channel(Operator):
    bl_idname = 'character_designer.clear_body_transform_channel'
    bl_label = 'Clear Body Control Transform'
    bl_description = 'Clear this channel on the selected body controls'
    bl_options = {'UNDO'}

    channel: EnumProperty(items=(('LOCATION', 'Location', ''), ('SCALE', 'Scale', '')))

    @classmethod
    def poll(cls, context):
        from . import natural_pose_clear
        return natural_pose_clear.body_selected(context)

    def execute(self, context):
        operator = bpy.ops.pose.loc_clear if self.channel == 'LOCATION' else bpy.ops.pose.scale_clear
        return operator('EXEC_DEFAULT', False)


CLASSES = (CHARACTERDESIGNER_OT_limb_ik_clear_wrist_rotation,
           CHARACTERDESIGNER_OT_clear_body_transform_channel, *limb_ik_wrist_move.CLASSES)


def _register_keymap():
    if _KEYMAPS:
        return False
    manager = getattr(bpy.context, 'window_manager', None)
    config = getattr(getattr(manager, 'keyconfigs', None), 'addon', None)
    if config is None:
        return False
    km = config.keymaps.get('Pose')
    if km is None:
        km = config.keymaps.new(name='Pose', space_type='EMPTY', region_type='WINDOW')
    if km.space_type != 'EMPTY' or km.region_type != 'WINDOW':
        raise RuntimeError('The Pose keymap has an unexpected editor context.')
    item = km.keymap_items.new(CLASSES[0].bl_idname, 'R', 'PRESS', alt=True, head=True)
    item.properties.reference = 'FOREARM'
    _KEYMAPS.append((km, item))
    item = km.keymap_items.new(limb_ik_wrist_move.CHARACTERDESIGNER_OT_grab_wrist.bl_idname,
                              'G', 'PRESS', head=True)
    _KEYMAPS.append((km, item))
    return True


def _remove_keymap():
    for km, item in reversed(_KEYMAPS):
        try:
            if any(existing == item for existing in km.keymap_items):
                km.keymap_items.remove(item)
        except ReferenceError:
            pass  # The native keymap has already been removed.
    _KEYMAPS.clear()


def _registered(cls):
    return getattr(cls, 'is_registered', 'bl_rna' in cls.__dict__)


def register():
    from . import natural_bind_mode
    added = []
    keymap_added = False
    try:
        for cls in CLASSES:
            if not _registered(cls):
                bpy.utils.register_class(cls)
                added.append(cls)
        if limb_ik_wrist_move.CHARACTERDESIGNER_OT_native_wrist_move in added:
            limb_ik_wrist_move.register()
        keymap_added = _register_keymap()
        natural_bind_mode.register()
    except Exception:
        if keymap_added or added:
            _remove_keymap()
        if limb_ik_wrist_move.CHARACTERDESIGNER_OT_native_wrist_move in added:
            limb_ik_wrist_move.unregister()
        for cls in reversed(added):
            bpy.utils.unregister_class(cls)
        raise


def unregister():
    from . import natural_bind_mode
    natural_bind_mode.unregister()
    _remove_keymap()
    limb_ik_wrist_move.unregister()
    for cls in reversed(CLASSES):
        if _registered(cls):
            bpy.utils.unregister_class(cls)
