import bpy


class LanguageSwitcherPanel(bpy.types.Panel):
    bl_label = "Language_Panel"
    bl_idname = "OBJECT_PT_action_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Language Change"

    def draw(self, context):
        layout = self.layout
        layout.operator("language_switcher.switch_to_chinese", text="English")
        layout.operator("language_switcher.switch_to_english", text="Chinese")
