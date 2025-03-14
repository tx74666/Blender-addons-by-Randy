import bpy
from .utils import util


class SwitchToEnglish(bpy.types.Operator):
    bl_idname = ""
    bl_label = ""

    def execute(self, context):
        return {"FINISHED"}
