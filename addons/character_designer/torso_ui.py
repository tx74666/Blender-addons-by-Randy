"""Compact body-page entry for reversible controls on the existing spine."""
import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel

from . import character_setup, bone_collections, torso_controls, limb_ik, spine_ik_fk
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


class CHARACTERDESIGNER_OT_spine_ik_fk(Operator):
    bl_idname = 'character_designer.spine_ik_fk'
    bl_label = 'Spine IK / FK'
    bl_description = 'Match the current spine pose before switching controls; choose the mode before animating'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('BUILD', 'Add Spine IK / FK', ''),
                                ('SWITCH', 'Match and Switch', ''),
                                ('RESET', 'Reset Spine Pose', 'Return the entire spine to its native neutral pose relative to Hips'),
                                ('SELECT', 'Select Spine IK Control', ''),
                                ('REMOVE', 'Remove Spine IK / FK', '')))
    mode: EnumProperty(items=(('FK', 'FK', 'Rotate the spine sections'),
                              ('IK', 'IK', 'Move the chest target')), default='IK')
    bone: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (context.object is not None and context.object.type == 'ARMATURE'
                and context.mode in {'OBJECT', 'POSE'})

    def execute(self, context):
        rig, select = context.object, None
        try:
            if self.action == 'BUILD':
                spine_ik_fk.build(context, rig)
            elif self.action == 'SWITCH':
                spine_ik_fk.switch(context, rig, self.mode,
                                   keyframe=context.scene.tool_settings.use_keyframe_insert_auto)
                record = spine_ik_fk.get_record(rig)
                select = record['chest'] if self.mode == 'IK' else record['fk_controls'][record['sources'][-1]]
            elif self.action == 'REMOVE':
                spine_ik_fk.remove(context, rig)
                select = torso_controls.get_record(rig)['bend']
            elif self.action == 'RESET':
                spine_ik_fk.reset(context, rig)
            else:
                record = spine_ik_fk.validate(rig)
                if not record or self.bone not in {record['chest'], record['shape']}:
                    raise ValueError('Choose an existing Spine IK control.')
                select = self.bone
            if select:
                limb_ik._mode_set(context, rig, 'POSE')
                for pb in rig.pose.bones:
                    pb.select = pb.name == select
                rig.data.bones.active = rig.data.bones[select]
        except (ValueError, RuntimeError, TypeError, ReferenceError, limb_ik.LimbIKError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        if self.action != 'SELECT':
            self.report({'INFO'}, 'Spine returned to its neutral pose relative to Hips.' if self.action == 'RESET'
                        else 'Spine IK / FK updated; current pose preserved.')
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
                extension = spine_ik_fk.validate(rig)
                current = spine_ik_fk.mode_for_rig(rig)
                if extension:
                    if current == 'BLEND':
                        box = layout.box()
                        box.alert = True
                        value = rig.pose.bones[extension['chest']][spine_ik_fk.PROPERTY]
                        box.label(text=f'Blended pose ({value:.3g}); controls may differ.', icon='INFO')
                        box.label(text='Match to a mode to continue posing.')
                    row = layout.row(align=True)
                    for mode in ('FK', 'IK'):
                        op = row.operator('character_designer.spine_ik_fk',
                                          text=('Match to ' + mode) if current == 'BLEND' else mode,
                                          depress=current == mode)
                        op.action, op.mode = 'SWITCH', mode
                    layout.label(text='Choose the mode before animating.', icon='INFO')
                if current != 'IK':
                    op = layout.operator('character_designer.torso_controls', text='Bend Spine', icon='CON_ROTLIKE')
                    op.action, op.bone = 'SELECT', record['bend']
                    row = layout.row(align=True)
                    for source in record['sources']:
                        op = row.operator('character_designer.torso_controls', text=source)
                        op.action, op.bone = 'SELECT', record['controls'][source]
                    layout.label(text='Rotate together; refine each section.', icon='INFO')
                if extension and current != 'FK':
                    for key, label in (('chest', 'Chest IK'), ('shape', 'Spine Shape')):
                        op = layout.operator('character_designer.spine_ik_fk', text=label)
                        op.action, op.bone = 'SELECT', extension[key]
                    layout.label(text='Chest: G / R; Shape: R.', icon='INFO')
                    layout.label(text='Straight spine: bend Shape slightly first.')
                if extension:
                    layout.operator('character_designer.spine_ik_fk', text='Reset Spine Pose', icon='LOOP_BACK').action = 'RESET'
                    row = layout.row()
                    row.alert = True
                    row.operator('character_designer.spine_ik_fk', text='Remove Spine IK / FK', icon='TRASH').action = 'REMOVE'
                else:
                    row = layout.row()
                    row.alert = True
                    row.operator('character_designer.spine_ik_fk', text='Add Spine IK / FK', icon='CON_KINEMATIC').action = 'BUILD'
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


TORSO_UI_CLASSES = (CHARACTERDESIGNER_OT_torso_controls, CHARACTERDESIGNER_OT_spine_ik_fk,
                    CHARACTERDESIGNER_PT_torso_controls)
