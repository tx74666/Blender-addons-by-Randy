"""Compact body-page entry for reversible controls on the existing spine."""
import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel

from . import character_setup, bone_collections, torso_controls, limb_ik
from .ui_constants import SIDEBAR_CATEGORY, rig_page_active


class CHARACTERDESIGNER_OT_torso_controls(Operator):
    bl_idname = 'character_designer.torso_controls'
    bl_label = 'Spine Controls'
    bl_description = 'Add or remove controls on the existing spine, keeping its current pose and weights'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('BUILD', 'Add Spine Controls', ''), ('REMOVE', 'Remove Spine Controls', ''), ('SELECT', 'Select Spine Control', '')))
    bone: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        rig = context.active_object
        if rig is None or rig.type != 'ARMATURE' or context.mode not in {'OBJECT', 'POSE'}:
            self.report({'WARNING'}, 'Select the main armature in Object or Pose Mode.')
            return {'CANCELLED'}
        try:
            if self.action == 'SELECT':
                record = torso_controls.get_record(rig)
                if not record or self.bone not in {*record['controls'].values(), record['bend']}:
                    raise ValueError('Select an existing spine control.')
                limb_ik._mode_set(context, rig, 'POSE')
                for pb in rig.pose.bones:
                    pb.select = pb.name == self.bone
                rig.data.bones.active = rig.data.bones[self.bone]
                return {'FINISHED'}
            layout = bone_collections.capture_managed_layout(rig)
            if self.action == 'BUILD':
                hips = character_setup.resolve_bone(context, 'HIPS', rig)
                torso_controls.build(context, rig, hips_name=hips)
            else:
                torso_controls.remove(context, rig)
            bone_collections.finish_rig_edit(rig, layout)
        except (ValueError, RuntimeError, TypeError, ReferenceError, limb_ik.LimbIKError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Spine controls ' + ('added' if self.action == 'BUILD' else 'removed') + '; pose and weights preserved.')
        return {'FINISHED'}


class CHARACTERDESIGNER_PT_torso_controls(Panel):
    bl_idname = 'CHARACTERDESIGNER_PT_torso_controls'
    bl_label = 'Spine Controls'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return rig_page_active(context, 'BODY')

    def draw(self, context):
        layout = self.layout
        rig = context.active_object
        if rig is None or rig.type != 'ARMATURE':
            layout.label(text='Select the main armature.', icon='INFO')
            return
        try:
            record = torso_controls.get_record(rig)
            if record:
                torso_controls.validate(rig)
                op = layout.operator('character_designer.torso_controls', text='Bend Spine', icon='CON_ROTLIKE')
                op.action, op.bone = 'SELECT', record['bend']
                row = layout.row(align=True)
                for source in record['sources']:
                    op = row.operator('character_designer.torso_controls', text=source)
                    op.action, op.bone = 'SELECT', record['controls'][source]
                layout.label(text='Bend together; refine each section.', icon='INFO')
                row = layout.row()
                row.alert = True
                row.operator('character_designer.torso_controls', text='Remove Spine Controls', icon='TRASH').action = 'REMOVE'
            else:
                layout.label(text='Uses your existing spine and Hips.', icon='BONE_DATA')
                row = layout.row()
                row.enabled = context.mode in {'OBJECT', 'POSE'}
                row.operator('character_designer.torso_controls', text='Add Spine Controls', icon='CON_KINEMATIC').action = 'BUILD'
        except (ValueError, RuntimeError, KeyError, limb_ik.LimbIKError) as exc:
            layout.label(text=str(exc), icon='ERROR')


TORSO_UI_CLASSES = (CHARACTERDESIGNER_OT_torso_controls, CHARACTERDESIGNER_PT_torso_controls)
