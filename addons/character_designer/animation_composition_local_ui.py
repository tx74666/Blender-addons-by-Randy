"""UI for the local source branch of Animation Composition."""

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator

from . import animation_composition_local as composition
from . import animation_weapon_grip as grip
from . import animation_weapon_profiles as profiles
from .equipment_ui import _target
from .animation_loop_timing import is_loop


class CHARACTERDESIGNER_OT_weapon_overlay(Operator):
    bl_idname = 'character_designer.weapon_overlay'
    bl_label = 'Add Upper-Body Action'
    bl_description = 'Place a filtered copy of an Action above the current locomotion Base'
    bl_options = {'REGISTER', 'UNDO'}

    action: StringProperty(name='Overlay Action')
    slot_handle: IntProperty(name='Object Slot Handle', min=0)
    frame: FloatProperty(name='Start Frame', default=1)
    blend_in: FloatProperty(name='Fade In', default=3, min=0)
    blend_out: FloatProperty(name='Fade Out', default=3, min=0)
    hold: BoolProperty(name='Hold Last Pose')
    loop_base: BoolProperty(name='Loop Base Beyond Its End')
    use_grip: BoolProperty(name='Animate Left Grip')
    grip_weight: FloatProperty(name='Left Grip', default=1, min=0, max=1, subtype='FACTOR')

    def invoke(self, context, _event):
        self.frame = context.scene.frame_current + context.scene.frame_subframe
        rig = _target(context)
        self.loop_base = bool(rig and rig.animation_data and is_loop(rig.animation_data.action))
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop_search(self, 'action', bpy.data, 'actions')
        action = bpy.data.actions.get(self.action)
        if action and sum(slot.target_id_type == 'OBJECT' for slot in action.slots) > 1:
            for slot in action.slots:
                if slot.target_id_type == 'OBJECT':
                    self.layout.label(text=f'{slot.identifier} ({slot.handle})')
            self.layout.prop(self, 'slot_handle')
        self.layout.prop(self, 'frame')
        self.layout.prop(self, 'blend_in')
        self.layout.prop(self, 'hold')
        if composition.session(_target(context)) is None:
            self.layout.prop(self, 'loop_base')
        if not self.hold:
            self.layout.prop(self, 'blend_out')
        record = profiles.attachment(_target(context))
        if record and record.get(grip.BINDING) and record[profiles.LOCAL].get('two_handed'):
            self.layout.prop(self, 'use_grip')
            if self.use_grip:
                self.layout.prop(self, 'grip_weight')

    def execute(self, context):
        try:
            composition.add_overlay(context, _target(context), bpy.data.actions.get(self.action),
                handle=self.slot_handle, frame=self.frame, blend_in=self.blend_in,
                blend_out=0 if self.hold else self.blend_out, hold=self.hold,
                grip_weight=self.grip_weight if self.use_grip else None,
                loop_base=self.loop_base)
        except (ValueError, RuntimeError, TypeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_weapon_close_overlay(Operator):
    bl_idname = 'character_designer.weapon_close_overlay'
    bl_label = 'Return to Base Action'
    bl_description = 'Close this A+B stack and retain the filtered Actions'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            composition.close(context, _target(context))
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def draw_composition(layout, context):
    rig = _target(context)
    if rig is not None and rig.animation_data is not None:
        saved = composition.session(rig)
        if saved is not None:
            layout.label(text='Base · ' + saved['base']['action'].name, icon='NLA')
            for overlay in saved['overlays'].values():
                layout.label(text=overlay['action'].name, icon='ACTION')
        layout.operator('character_designer.weapon_overlay', icon='ADD')
        if saved is not None:
            layout.operator('character_designer.weapon_close_overlay', icon='LOOP_BACK')


LOCAL_COMPOSITION_CLASSES = (
    CHARACTERDESIGNER_OT_weapon_overlay,
    CHARACTERDESIGNER_OT_weapon_close_overlay,
)
