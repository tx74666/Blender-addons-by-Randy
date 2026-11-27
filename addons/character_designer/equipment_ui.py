"""Miscellaneous sidebar controls for local Weapon Profiles."""

import bpy
from bpy.props import BoolProperty, FloatVectorProperty, StringProperty
from bpy.types import Operator, Panel

from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_MISC, active_ui_page

from . import animation_weapon_profiles as profiles
from . import animation_weapon_grip as grip


def _target(context):
    from .animation import _main_rig, _main_rig_error, _target as animation_target

    rig = animation_target(context)
    if rig is not None:
        return rig
    main = _main_rig(context)
    return main if main is not None and not _main_rig_error(context, main) else None


def _settings(context):
    from .animation import _settings as animation_settings

    return animation_settings(context)


def _selected(context):
    profile = _settings(context).weapon_profile
    record = profiles.attachment(_target(context))
    return profile or (record[profiles.LOCAL] if record is not None else None)


class CHARACTERDESIGNER_OT_weapon_import(Operator):
    bl_idname = 'character_designer.weapon_import'
    bl_label = 'Import Weapon'
    bl_description = 'Import a static FBX, OBJ, glTF or GLB as a reusable Weapon Profile'
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.fbx;*.obj;*.gltf;*.glb', options={'HIDDEN'})

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            _settings(context).weapon_profile = profiles.import_profile(context, self.filepath)
        except (ValueError, RuntimeError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_weapon_attach(Operator):
    bl_idname = 'character_designer.weapon_attach'
    bl_label = 'Attach to Right Hand'
    bl_description = 'Mount the selected Weapon Profile to a right-hand bone'
    bl_options = {'REGISTER', 'UNDO'}

    profile_name: StringProperty(options={'HIDDEN'})
    hand: StringProperty(name='Right Hand')

    @classmethod
    def poll(cls, context):
        return _target(context) is not None

    def invoke(self, context, _event):
        profile = _selected(context)
        if profile is None:
            self.report({'ERROR'}, 'Select a Weapon Profile.')
            return {'CANCELLED'}
        self.profile_name = profile.name
        self.hand = profiles.right_hand(_target(context))
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop_search(self, 'hand', _target(context).data, 'bones')

    def execute(self, context):
        try:
            profile = bpy.data.collections.get(self.profile_name) if self.profile_name else _selected(context)
            profiles.attach(context, _target(context), profile, self.hand)
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_weapon_detach(Operator):
    bl_idname = 'character_designer.weapon_detach'
    bl_label = 'Detach Weapon'
    bl_description = 'Remove the active attachment and keep its reusable profile'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return profiles.attachment(_target(context)) is not None

    def execute(self, context):
        try:
            profiles.detach(context, _target(context))
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_weapon_calibrate(Operator):
    bl_idname = 'character_designer.weapon_calibrate'
    bl_label = 'Edit Weapon Profile'
    bl_description = 'Save right-hand mount and left-hand grip offsets to this reusable profile'
    bl_options = {'REGISTER', 'UNDO'}

    profile_name: StringProperty(options={'HIDDEN'})
    label: StringProperty(name='Name')
    two_handed: BoolProperty(name='Two-Handed')
    mount_location: FloatVectorProperty(name='Mount Position', subtype='TRANSLATION', unit='LENGTH')
    mount_rotation: FloatVectorProperty(name='Mount Rotation', subtype='EULER', unit='ROTATION')
    grip_location: FloatVectorProperty(name='Grip Position', subtype='TRANSLATION', unit='LENGTH')
    grip_rotation: FloatVectorProperty(name='Grip Rotation', subtype='EULER', unit='ROTATION')

    def invoke(self, context, _event):
        profile = _selected(context)
        if profile is None:
            self.report({'ERROR'}, 'Select a Weapon Profile.')
            return {'CANCELLED'}
        self.profile_name = self.label = profile.name
        self.two_handed = bool(profile.get('two_handed'))
        for field, value in profiles._calibration(profile).items():
            setattr(self, field, value)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, _context):
        self.layout.prop(self, 'label')
        self.layout.prop(self, 'mount_location')
        self.layout.prop(self, 'mount_rotation')
        self.layout.prop(self, 'two_handed')
        if self.two_handed:
            self.layout.prop(self, 'grip_location')
            self.layout.prop(self, 'grip_rotation')

    def execute(self, context):
        try:
            profile = bpy.data.collections.get(self.profile_name) if self.profile_name else _selected(context)
            profiles.update_profile(context, _target(context), profile,
                {field: getattr(self, field) for field in profiles.CALIBRATION},
                two_handed=self.two_handed)
            if self.label.strip():
                profile.name = self.label.strip()
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_weapon_grip(Operator):
    bl_idname = 'character_designer.weapon_grip'
    bl_label = 'Enable Left Grip'
    bl_options = {'REGISTER', 'UNDO'}

    disable: BoolProperty(options={'HIDDEN'})

    def execute(self, context):
        rig = _target(context)
        try:
            if rig is None:
                raise ValueError('Choose the character rig.')
            if self.disable:
                profiles.require_idle(context, rig)
                record = profiles.attachment(rig)
                if record is not None:
                    grip.disable(rig, record)
            else:
                grip.enable(context, rig)
            context.view_layer.update()
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def draw_weapons(layout, context):
    rig = _target(context)
    record = profiles.attachment(rig)
    row = layout.row(align=True)
    row.prop(_settings(context), 'weapon_profile', text='')
    row.operator('character_designer.weapon_import', text='', icon='IMPORT')
    if _selected(context) is not None:
        row = layout.row(align=True)
        row.operator('character_designer.weapon_calibrate', text='Edit Profile')
        if not record:
            row.operator('character_designer.weapon_attach', text='Attach')
    if record is not None:
        row = layout.row(align=True)
        row.label(text=record[profiles.LOCAL].name + ' · ' + record['hand'], icon='CONSTRAINT_BONE')
        row.operator('character_designer.weapon_detach', text='', icon='X')
        if record[profiles.LOCAL].get('two_handed'):
            if record.get(grip.BINDING):
                row = layout.row(align=True)
                row.prop(rig, grip.PATH, text='Left Grip', slider=True)
                row.operator('character_designer.weapon_grip', text='', icon='X').disable = True
            else:
                layout.operator('character_designer.weapon_grip', icon='CON_KINEMATIC')


class CHARACTERDESIGNER_PT_equipment(Panel):
    bl_label = 'Weapon'
    bl_idname = 'CHARACTERDESIGNER_PT_equipment'
    bl_parent_id = 'CHARACTERDESIGNER_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_MISC

    def draw(self, context):
        from . import animation_composition_local_ui, animation_composition_ui, animation_preview_ui
        from .animation_original_inputs_ui import original_inputs_live
        from .animation_physics_ui import section

        layout = self.layout
        draw_weapons(layout, context)
        rig, preview = _target(context), animation_preview_ui.session.status(context)
        local = None
        if rig is not None and rig.animation_data is not None:
            local = animation_composition_local_ui.composition.session(rig)
            overlay = section(layout, 'character_designer_animation_upper_body', 'Upper-Body Composition',
                              icon='NLA', default_closed=local is None)
            if overlay is not None:
                overlay.enabled = preview is None
                animation_composition_local_ui.draw_composition(overlay, context)

        worklist = getattr(context.scene, 'character_designer_animation_worklist', None)
        if worklist is not None and worklist.workspace_path:
            overlay = layout.column()
            overlay.enabled = preview is None and local is None and not original_inputs_live(context)
            animation_composition_ui.draw_composition(overlay, context)


EQUIPMENT_CLASSES = (
    CHARACTERDESIGNER_OT_weapon_import,
    CHARACTERDESIGNER_OT_weapon_attach,
    CHARACTERDESIGNER_OT_weapon_detach,
    CHARACTERDESIGNER_OT_weapon_calibrate,
    CHARACTERDESIGNER_OT_weapon_grip,
    CHARACTERDESIGNER_PT_equipment,
)
