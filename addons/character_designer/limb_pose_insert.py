"""Augment plain viewport Insert Keyframe only while a selected limb switch awaits a key."""
import bpy

from . import limb_ik_fk_pose_key as pose_key


_KEYMAP_ITEMS = []
_OPERATOR = 'character_designer.insert_limb_switch'


class CHARACTERDESIGNER_OT_insert_limb_switch(bpy.types.Operator):
    bl_idname = _OPERATOR
    bl_label = 'Insert Keyframe with Limb Mode'
    bl_options = {'UNDO', 'INTERNAL'}

    @classmethod
    def register(cls):
        addon = bpy.context.window_manager.keyconfigs.addon
        if addon and not _KEYMAP_ITEMS:
            km = addon.keymaps.new(name='Pose', space_type='EMPTY', region_type='WINDOW')
            for value in ('PRESS', 'CLICK'):
                item = km.keymap_items.new(cls.bl_idname, 'I', value, head=True)
                _KEYMAP_ITEMS.append((km, item))

    @classmethod
    def unregister(cls):
        while _KEYMAP_ITEMS:
            km, item = _KEYMAP_ITEMS.pop()
            km.keymap_items.remove(item)

    @classmethod
    def poll(cls, context):
        return bool(pose_key.selected_pending(context))

    def invoke(self, context, event):
        # Respect remapped/disabled Insert and Blender's optional click-drag pie.
        km = context.window_manager.keyconfigs.user.keymaps.get('Pose')
        if km:
            for item in km.keymap_items:
                if (item.active and item.idname != _OPERATOR and item.type == 'I'
                        and item.value in {event.value, 'ANY'} and item.key_modifier == 'NONE'
                        and (item.any or not (item.ctrl or item.alt or item.shift or item.oskey))):
                    if item.idname == 'anim.keyframe_insert':
                        return self.execute(context)
                    break
        return {'PASS_THROUGH'}

    def execute(self, context):
        tokens = pose_key.selected_pending(context)
        if not tokens:
            return {'PASS_THROUGH'}
        try:
            warning = pose_key.insert_with_switches(context, tokens)
        except (KeyError, ReferenceError, RuntimeError, TypeError, ValueError) as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}
        if warning:
            self.report({'WARNING'}, warning)
        return {'FINISHED'}
