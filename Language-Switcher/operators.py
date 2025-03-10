import bpy
from .utils import set_language


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
