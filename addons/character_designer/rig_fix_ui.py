"""Small placement and display adjustments, separate from rig construction."""
from bpy.props import EnumProperty
from bpy.types import Operator, Panel

from .ui_constants import SIDEBAR_CATEGORY


def _local_rig(context):
    rig = getattr(context, 'object', None)
    return bool(rig and rig.type == 'ARMATURE'
                and context.mode in {'OBJECT', 'POSE'}
                and rig.is_editable and not rig.library
                and not rig.override_library and not rig.data.library)


class CHARACTERDESIGNER_OT_fix_show_body_controls(Operator):
    bl_idname = 'character_designer.fix_show_body_controls'
    bl_label = 'Show Body Controls'
    bl_description = 'Show existing body controls and their custom shapes without moving or rebuilding the rig'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import body_original_mode

        return _local_rig(context) and not body_original_mode.active(context.object)

    def execute(self, context):
        from . import bone_display

        try:
            bone_display.show_controls(context, bone_display.character_rig(context),
                                       role='BODY', toggle=False)
            self.report({'INFO'}, 'Body controls shown; custom shapes kept.')
            return {'FINISHED'}
        except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_fix_rebuild_foot_controls(Operator):
    bl_idname = 'character_designer.fix_rebuild_foot_controls'
    bl_label = 'Rebuild Foot Controls'
    bl_description = 'Rebuild heel, ball and toe helpers at the current foot pose, keeping native bones, weights and custom control shapes'
    bl_options = {'REGISTER', 'UNDO'}

    side: EnumProperty(name='Feet', items=(
        ('BOTH', 'Both Feet', 'Rebuild the left and right foot controls'),
        ('L', 'Left Foot', 'Rebuild only the left foot controls'),
        ('R', 'Right Foot', 'Rebuild only the right foot controls'),
    ), default='BOTH')

    @classmethod
    def poll(cls, context):
        return _local_rig(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(self, 'side')
        self.layout.label(text='Uses the current foot pose.')
        self.layout.label(text='Keeps bones, weights and custom shapes.')

    def execute(self, context):
        from . import bone_display, foot_controls_rebuild

        try:
            sides = ('L', 'R') if self.side == 'BOTH' else (self.side,)
            foot_controls_rebuild.rebuild(context, bone_display.character_rig(context),
                                          sides=sides)
            label = 'both feet' if self.side == 'BOTH' else 'the left foot' if self.side == 'L' else 'the right foot'
            self.report({'INFO'}, f'Foot controls rebuilt for {label}; custom shapes kept.')
            return {'FINISHED'}
        except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def _draw_body_controls(layout, context):
    from . import bone_display, body_original_mode

    box = layout.box()
    box.label(text='Control Visibility', icon='HIDE_OFF')
    try:
        rig = bone_display.character_rig(context)
        if rig is None:
            box.label(text='Select the character or set Main Rig.', icon='INFO')
            return
        original = body_original_mode.active(rig)
        collection = bone_display.groups.body_collection(rig)
        row = box.row()
        row.enabled = bool(collection and not original
                           and CHARACTERDESIGNER_OT_fix_show_body_controls.poll(context))
        row.operator('character_designer.fix_show_body_controls',
                     text='Show Body Controls', icon='ARMATURE_DATA')
        if original:
            box.label(text='Choose Controls in Bone Display first.', icon='INFO')
        elif collection is None:
            box.label(text='Set up body controls first.', icon='INFO')
        box.label(text='Display only; keeps custom shapes.')
    except (ValueError, RuntimeError, TypeError, KeyError, ReferenceError) as exc:
        box.label(text=str(exc), icon='ERROR')


def draw_body_adjustments(layout, context):
    """Shared body display/placement tools; part rebuild lives in its selector."""
    from . import character_ground

    _draw_body_controls(layout, context)
    character_ground.draw(layout, context)


class CHARACTERDESIGNER_PT_rig_fix(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_rig_fix'
    bl_label = 'Fix'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_parent_id = 'CHARACTERDESIGNER_PT_main'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        # Retain the registered type for compatibility with old UI references.
        # Body owns the adjustments and the shared part selection now.
        return False

    def draw(self, context):
        draw_body_adjustments(self.layout, context)


RIG_FIX_UI_CLASSES = (
    CHARACTERDESIGNER_OT_fix_show_body_controls,
    CHARACTERDESIGNER_OT_fix_rebuild_foot_controls,
    CHARACTERDESIGNER_PT_rig_fix,
)
