"""Miscellaneous sidebar controls for local Weapon Profiles."""

import bpy
from bpy.props import BoolProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator, Panel

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


class CHARACTERDESIGNER_OT_weapon_import_unity_library(Operator):
    bl_idname = 'character_designer.weapon_import_unity_library'
    bl_label = 'Import Unity Library'
    bl_description = 'Add the captured Unity weapon models as reusable profiles and keep existing grip settings'
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import animation_weapon_library

        try:
            imported, reused, issues = animation_weapon_library.import_library(context, self.filepath)
        except (OSError, RuntimeError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        if not imported and not reused:
            self.report({'ERROR'}, issues[0] if issues else 'The Unity library contains no weapons.')
            return {'CANCELLED'}
        settings = _settings(context)
        if settings.weapon_profile is None:
            settings.weapon_profile = (imported or reused)[0]
        summary = f'Imported {len(imported)} weapons, reused {len(reused)}.'
        if issues:
            self.report({'WARNING'}, f'{summary} {len(issues)} unavailable. {issues[0]}')
        else:
            self.report({'INFO'}, summary)
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


class CHARACTERDESIGNER_OT_weapon_equip(Operator):
    bl_idname = 'character_designer.weapon_equip'
    bl_label = 'Equip'
    bl_description = 'Mount this weapon and add its Equip Action at the current frame when configured'
    bl_options = {'REGISTER', 'UNDO'}

    profile_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _target(context) is not None

    def execute(self, context):
        from . import animation_composition_local as composition

        rig = _target(context)
        profile = bpy.data.collections.get(self.profile_name)
        try:
            if not profiles.is_profile(profile):
                raise ValueError('Choose an available Weapon Profile.')
            current = profiles.attachment(rig)
            if current is None or current['scene'] != context.scene or current[profiles.LOCAL] != profile:
                with profiles.equip(context, rig, profile):
                    action = profile.get(profiles.EQUIP_ACTION)
                    if action is not None:
                        frame = context.scene.frame_current + context.scene.frame_subframe
                        composition.add_overlay(context, rig, action, handle=profile[profiles.EQUIP_SLOT],
                                                frame=frame, hold=True)
        except (ValueError, RuntimeError, TypeError, KeyError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        _settings(context).weapon_profile = profile
        return {'FINISHED'}


class CHARACTERDESIGNER_MT_weapon_equip(Menu):
    bl_idname = 'CHARACTERDESIGNER_MT_weapon_equip'
    bl_label = 'Equip'

    def draw(self, _context):
        choices = sorted((p for p in bpy.data.collections if profiles.is_profile(p)),
                         key=lambda p: p.name.casefold())
        if not choices:
            self.layout.label(text='Import a Weapon Profile in Miscellaneous → Weapon.')
        self.layout.operator_context = 'EXEC_DEFAULT'
        for profile in choices:
            action = profile.get(profiles.EQUIP_ACTION)
            text = f'{profile.name} · {action.name}' if action is not None else profile.name
            self.layout.operator('character_designer.weapon_equip', text=text).profile_name = profile.name


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
    equip_action: StringProperty(name='Equip Action',
                                 description='Upper-body Action to add when equipping this weapon')
    equip_slot: IntProperty(name='Object Slot Handle', min=0)

    def invoke(self, context, _event):
        profile = _selected(context)
        if profile is None:
            self.report({'ERROR'}, 'Select a Weapon Profile.')
            return {'CANCELLED'}
        self.profile_name = self.label = profile.name
        self.two_handed = bool(profile.get('two_handed'))
        action = profile.get(profiles.EQUIP_ACTION)
        self.equip_action = action.name if action is not None else ''
        self.equip_slot = profile.get(profiles.EQUIP_SLOT, 0)
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
        self.layout.prop_search(self, 'equip_action', bpy.data, 'actions')
        action = bpy.data.actions.get(self.equip_action)
        if action and sum(slot.target_id_type == 'OBJECT' for slot in action.slots) > 1:
            for slot in action.slots:
                if slot.target_id_type == 'OBJECT':
                    self.layout.label(text=f'{slot.identifier} ({slot.handle})')
            self.layout.prop(self, 'equip_slot')

    def execute(self, context):
        from . import animation_composition_local as composition

        try:
            profile = bpy.data.collections.get(self.profile_name) if self.profile_name else _selected(context)
            edit_action = self.properties.is_property_set('equip_action')
            action = bpy.data.actions.get(self.equip_action) if edit_action and self.equip_action else None
            if edit_action and self.equip_action and (action is None or action.library or action.get(composition.DRAFT)):
                raise ValueError('Choose a local source Action for Equip Action.')
            multi_slot = action is not None and sum(slot.target_id_type == 'OBJECT' for slot in action.slots) > 1
            handle = composition._unique_slot(action, self.equip_slot if multi_slot else 0) if action is not None else 0
            profiles.update_profile(context, _target(context), profile,
                {field: getattr(self, field) for field in profiles.CALIBRATION},
                two_handed=self.two_handed)
            if action is not None:
                profile[profiles.EQUIP_ACTION], profile[profiles.EQUIP_SLOT] = action, handle
            elif edit_action:
                profile.pop(profiles.EQUIP_ACTION, None)
                profile.pop(profiles.EQUIP_SLOT, None)
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


def draw_equip(layout, rig):
    record = profiles.attachment(rig)
    row = layout.row(align=True)
    row.menu(CHARACTERDESIGNER_MT_weapon_equip.bl_idname,
             text=record[profiles.LOCAL].name if record else 'Equip', icon='OUTLINER_COLLECTION')
    if record is not None:
        row.operator('character_designer.weapon_detach', text='', icon='X')


def draw_weapons(layout, context):
    rig = _target(context)
    record = profiles.attachment(rig)
    row = layout.row(align=True)
    row.prop(_settings(context), 'weapon_profile', text='')
    row.operator('character_designer.weapon_import', text='', icon='IMPORT')
    layout.operator('character_designer.weapon_import_unity_library', icon='ASSET_MANAGER')
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

        layout = self.layout
        draw_weapons(layout, context)
        rig, preview = _target(context), animation_preview_ui.session.status(context)
        local = None
        if rig is not None and rig.animation_data is not None:
            local = animation_composition_local_ui.composition.session(rig)

        worklist = getattr(context.scene, 'character_designer_animation_worklist', None)
        if worklist is not None and worklist.workspace_path:
            overlay = layout.column()
            overlay.enabled = preview is None and local is None and not original_inputs_live(context)
            animation_composition_ui.draw_composition(overlay, context)


EQUIPMENT_CLASSES = (
    CHARACTERDESIGNER_OT_weapon_import,
    CHARACTERDESIGNER_OT_weapon_import_unity_library,
    CHARACTERDESIGNER_OT_weapon_attach,
    CHARACTERDESIGNER_OT_weapon_detach,
    CHARACTERDESIGNER_OT_weapon_equip,
    CHARACTERDESIGNER_MT_weapon_equip,
    CHARACTERDESIGNER_OT_weapon_calibrate,
    CHARACTERDESIGNER_OT_weapon_grip,
    CHARACTERDESIGNER_PT_equipment,
)
