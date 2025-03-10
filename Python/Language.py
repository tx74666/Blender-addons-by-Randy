import bpy

bl_info = {
    "name": "Language Switcher",
    "author": "NORMAL",
    "version": (1, 0, 0),
    "blender": (4, 3, 2),
    "location": "View3D > Sidebar > Language Change",
    "description": "Quickly switch Blender UI language between English and Chinese.",
    "warning": "",
    "category": "Interface",
}


def set_language(language_code):
    bpy.context.preferences.view.language = language_code
    bpy.context.preferences.view.use_translate_interface = True
    bpy.context.preferences.view.use_translate_tooltips = True
    bpy.context.preferences.view.use_translate_new_dataname = False
    bpy.ops.wm.save_userpref()


class SwitchToEnglish(bpy.types.Operator):
    bl_idname = "language_switcher.switch_to_chinese"
    bl_label = "Switch to Chinese"

    def execute(self, context):
        set_language("zh_HANS")
        return {"FINISHED"}


class SwitchToChinese(bpy.types.Operator):
    bl_idname = "language_switcher.switch_to_english"
    bl_label = "Switch to English"

    def execute(self, context):
        set_language("en_US")
        return {"FINISHED"}


class LanguageSwitcherPanel(bpy.types.Panel):
    bl_label = "Actions"
    bl_idname = "OBJECT_PT_action_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Language Change"

    def draw(self, context):
        layout = self.layout

        layout.operator("language_switcher.switch_to_chinese", text="English")
        layout.operator("language_switcher.switch_to_english", text="Chinese")


classes = [SwitchToEnglish, SwitchToChinese, LanguageSwitcherPanel]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
