import bpy
from .operators import SwitchToEnglish, SwitchToChinese
from .ui import LanguageSwitcherPanel

bl_info = {
    "name": "Language Switcher",
    "author": "Randy Goodman",
    "version": (1, 0, 0),
    "blender": (4, 3, 2),
    "location": "View3D > Sidebar > Language Change",
    "description": "Quickly switch Blender UI language between English and Chinese.",
    "warning": "",
    "category": "Interface",
}

classes = [SwitchToEnglish, SwitchToChinese, LanguageSwitcherPanel]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
