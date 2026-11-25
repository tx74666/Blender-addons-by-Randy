"""Character motion picker beside the native Action Editor menus."""
import bpy


def draw_motion_button(self, context):
    if CD_PT_motion_picker.poll(context):
        self.layout.popover(panel=CD_PT_motion_picker.bl_idname, text='Motions', icon='ACTION')


class CD_PT_motion_picker(bpy.types.Panel):
    bl_idname = 'CD_PT_motion_picker'
    bl_label = 'Character Motions'
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 18

    @classmethod
    def poll(cls, context):
        from .animation_worklist_ui import motion_picker_available
        return getattr(context.space_data, 'mode', '') == 'ACTION' and motion_picker_available(context)

    def draw(self, context):
        from . import animation_preview_ui
        from .animation_worklist_ui import draw_motion_picker
        preview = animation_preview_ui.session.status(context)
        if preview is None:
            draw_motion_picker(self.layout, context)
        else:
            animation_preview_ui.draw(self.layout, context, preview)

    @classmethod
    def register(cls):
        bpy.types.DOPESHEET_MT_editor_menus.append(draw_motion_button)

    @classmethod
    def unregister(cls):
        bpy.types.DOPESHEET_MT_editor_menus.remove(draw_motion_button)
