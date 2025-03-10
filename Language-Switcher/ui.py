import bpy


class LanguageSwitcherPanel(bpy.types.Panel):
    bl_label = "Language_Panel"
    bl_idname = "OBJECT_PT_action_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Language Change"

    def draw(self, context):
        layout = self.layout
        current_language = bpy.context.preferences.view.language
        if current_language == "en_US":
            layout.operator(
                "language_switcher.switch_to_chinese", text="Switch to Chinese"
            )
        else:
            layout.operator(
                "language_switcher.switch_to_english", text="Switch to English"
            )
