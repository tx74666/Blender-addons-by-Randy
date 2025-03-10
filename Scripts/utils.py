import bpy


def set_language(language_code):
    bpy.context.preferences.view.language = language_code
    bpy.context.preferences.view.use_translate_interface = True
    bpy.context.preferences.view.use_translate_tooltips = True
    bpy.context.preferences.view.use_translate_new_dataname = False
    bpy.ops.wm.save_userpref()
