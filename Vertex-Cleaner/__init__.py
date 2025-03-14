import bpy

bl_info = {
    "name": "",
    "author": "Randy Goodman",
    "version": (1, 0, 0),
    "blender": (4, 3, 2),
    "location": "View3D > Sidebar > Language Change",
    "description": "",
    "warning": "",
    "category": "Interface",
}

classes = []


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
