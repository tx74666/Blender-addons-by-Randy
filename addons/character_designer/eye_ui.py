"""Small Rig / Body entry for head-following gaze controls."""
import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator, Panel

from . import eye_controls, limb_ik
from .ui_constants import SIDEBAR_CATEGORY, rig_page_active


class CHARACTERDESIGNER_OT_eye_controls(Operator):
    bl_idname = 'character_designer.eye_controls'
    bl_label = 'Eye Controls'
    bl_description = 'Move the shared mask to aim both eyes, or move either circle independently'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('BUILD', 'Add Eye Controls', ''),
                                ('REMOVE', 'Remove Eye Controls', ''),
                                ('SPACING', 'Display Spacing', 'Move the clickable outlines forward without changing the gaze'),
                                ('SELECT', 'Select Eye Control', '')))
    bone: StringProperty(options={'SKIP_SAVE'})
    head_name: StringProperty(name='Head')
    left_name: StringProperty(name='Left Eye')
    right_name: StringProperty(name='Right Eye')
    distance: FloatProperty(name='Forward Offset', default=0.0, min=0.0,
        soft_max=1.0, precision=3,
        description='Extra forward display distance in armature units; zero restores the original display positions')

    @classmethod
    def poll(cls, context):
        return (context.object is not None and context.object.type == 'ARMATURE'
                and context.mode in {'OBJECT', 'POSE'})

    def invoke(self, context, _event):
        if self.action == 'SPACING':
            try:
                current = eye_controls.display_spacing(context.object)
                self.distance = current or eye_controls.recommended_display_spacing(context.object)
            except (ValueError, RuntimeError, limb_ik.LimbIKError) as exc:
                self.report({'WARNING'}, str(exc))
                return {'CANCELLED'}
            return context.window_manager.invoke_props_dialog(self, width=360)
        if self.action == 'BUILD':
            try:
                eye_controls.resolve_eyes(context, context.object,
                    self.head_name or None, self.left_name or None, self.right_name or None)
            except (ValueError, RuntimeError, limb_ik.LimbIKError):
                return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        if self.action == 'SPACING':
            self.layout.prop(self, 'distance')
            self.layout.label(text='0 restores the original display positions.')
            self.layout.label(text='Gaze and animation stay unchanged.', icon='INFO')
            self.layout.label(text='The transform gizmo stays at the target bone.')
            return
        self.layout.label(text='Choose the head and its two eye bones.')
        for name in ('head_name', 'left_name', 'right_name'):
            self.layout.prop_search(self, name, context.object.data, 'bones')

    def execute(self, context):
        rig = context.object
        try:
            if self.action == 'BUILD':
                eye_controls.build(context, rig, self.head_name or None,
                                   self.left_name or None, self.right_name or None)
            elif self.action == 'REMOVE':
                eye_controls.remove(context, rig)
            elif self.action == 'SPACING':
                eye_controls.set_display_spacing(context, rig, self.distance)
                self.report({'INFO'}, 'Eye display spacing updated; gaze unchanged.')
                return {'FINISHED'}
            else:
                record = eye_controls.validate(rig)
                if not record or self.bone not in record['bones'].values():
                    raise ValueError('Choose an existing eye control.')
                limb_ik._mode_set(context, rig, 'POSE')
                for pb in rig.pose.bones:
                    pb.select = pb.name == self.bone
                rig.data.bones.active = rig.data.bones[self.bone]
                return {'FINISHED'}
        except (ValueError, RuntimeError, TypeError, ReferenceError, limb_ik.LimbIKError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Eye controls added; move the mask or circles.' if self.action == 'BUILD'
                    else 'Eye controls removed; current gaze preserved.')
        return {'FINISHED'}


class CHARACTERDESIGNER_PT_eye_controls(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_eye_controls'
    bl_label = 'Eye Controls'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return rig_page_active(context, 'BODY')

    def draw(self, context):
        layout, rig = self.layout, context.object
        if rig is None or rig.type != 'ARMATURE':
            layout.label(text='Select the main armature.', icon='INFO')
            return
        try:
            record = eye_controls.validate(rig)
            if record:
                layout.label(text='Follows: ' + record['head'], icon='BONE_DATA')
                op = layout.operator('character_designer.eye_controls', text='Both Eyes', icon='HIDE_OFF')
                op.action, op.bone = 'SELECT', record['master']
                row = layout.row(align=True)
                for side, label in (('L', 'Left Eye'), ('R', 'Right Eye')):
                    op = row.operator('character_designer.eye_controls', text=label)
                    op.action, op.bone = 'SELECT', record['targets'][side]
                layout.label(text='G: aim; Alt+G: reset selected controls.', icon='INFO')
                layout.operator('character_designer.eye_controls', text='Display Spacing...',
                                icon='EMPTY_ARROWS').action = 'SPACING'
                row = layout.row()
                row.alert = True
                row.operator('character_designer.eye_controls', text='Remove Eye Controls', icon='TRASH').action = 'REMOVE'
            else:
                try:
                    head, left, right = eye_controls.resolve_eyes(context, rig)
                    layout.label(text=left + ' / ' + right, icon='BONE_DATA')
                    layout.label(text='Follows: ' + head)
                except (ValueError, RuntimeError, limb_ik.LimbIKError):
                    layout.label(text='Choose eye bones when adding controls.', icon='INFO')
                row = layout.row()
                row.alert = True
                row.operator('character_designer.eye_controls', text='Add Eye Controls', icon='CON_TRACKTO').action = 'BUILD'
        except (ValueError, RuntimeError, KeyError, limb_ik.LimbIKError) as exc:
            layout.label(text=str(exc), icon='ERROR')


EYE_UI_CLASSES = (CHARACTERDESIGNER_OT_eye_controls, CHARACTERDESIGNER_PT_eye_controls)
