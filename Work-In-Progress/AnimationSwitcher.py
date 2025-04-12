import bpy
import math

default_transform = {
    "location": (0, -5.716, 4.88852),
    "rotation": (-90, 0, 0),
    "scale": 3.125,
}

wind_settings = {
    "IDLE": {
        "label": "Idle Wind",
        "strength_expr": "15+8*sin(frame/11.9)",
        "use_driver": True,
        "action": "Idle-E",
        **default_transform,
    },
    "WALK": {
        "label": "Walk Wind",
        "use_driver": False,
        "strength_value": 16,
        "action": "Walk-E",
        **default_transform,
    },
    "RUN": {
        "label": "Run Wind",
        "use_driver": False,
        "strength_value": 20,
        "action": "Run-E",
        **default_transform,
    },
}


# Generate enum
def get_enum_items():
    items = []
    for key, value in wind_settings.items():
        desc = value.get("strength_expr", str(value.get("strength_value", "")))
        items.append((key, value["label"], desc))
    return items


# Operator: Apply wind settings
class WIND_ANIMATOR_OT_ApplyWind(bpy.types.Operator):
    bl_idname = "object.apply_wind_animation"
    bl_label = "Apply Wind"

    wind_type: bpy.props.EnumProperty(name="Wind Type", items=get_enum_items())

    def execute(self, context):
        obj = context.scene.simple_picker.target_object

        if not obj:
            self.report({"WARNING"}, "请先选择一个风的目标对象！")
            return {"CANCELLED"}

        if not obj.field or obj.field.type != "WIND":
            self.report({"WARNING"}, f"{obj.name} 不是风力对象！")
            return {"CANCELLED"}

        settings = wind_settings[self.wind_type]

        # Wind strength
        obj.field.driver_remove("strength")
        if settings["use_driver"]:
            driver = obj.field.driver_add("strength").driver
            driver.expression = settings["strength_expr"]
        else:
            obj.field.strength = settings["strength_value"]

        # Transform
        obj.location = settings.get("location", obj.location)
        rot = settings.get("rotation", obj.rotation_euler)
        obj.rotation_euler = tuple(math.radians(a) for a in rot)  # convert to radians
        scale = settings.get("scale", obj.scale)
        obj.scale = (scale, scale, scale)

        # Animation actions
        armature = bpy.data.objects.get("Armature_Elaina")
        action_name = settings.get("action")
        if armature and action_name:
            action = bpy.data.actions.get(action_name)
            if action:
                if armature.animation_data is None:
                    armature.animation_data_create()
                armature.animation_data.action = action
                context.scene.frame_start = int(action.frame_range[0])
                context.scene.frame_end = int(action.frame_range[1])
                context.scene.frame_current = int(action.frame_range[0])

        return {"FINISHED"}


# Property group to select target wind object
class SimplePickerProperties(bpy.types.PropertyGroup):
    target_object: bpy.props.PointerProperty(type=bpy.types.Object)


# UI Panel in the 3D view
class WIND_PT_Panel(bpy.types.Panel):
    bl_label = "Wind Control"
    bl_idname = "WIND_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pose"

    def draw(self, context):
        layout = self.layout
        props = context.scene.simple_picker

        for wind_key, wind_data in wind_settings.items():
            label = wind_data["label"]
            op = layout.operator("object.apply_wind_animation", text=label)
            op.wind_type = wind_key

        layout.separator()
        layout.prop(props, "target_object", text="Wind")


# Registration
classes = [WIND_ANIMATOR_OT_ApplyWind, WIND_PT_Panel, SimplePickerProperties]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.simple_picker = bpy.props.PointerProperty(
        type=SimplePickerProperties
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.simple_picker


if __name__ == "__main__":
    register()
