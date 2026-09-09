"""Saved character references and the two common weight binding actions."""

import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from .ui_constants import (
    SIDEBAR_CATEGORY, UI_PAGE_RIG,
    UI_PAGE_WEIGHT, active_ui_page,
)

ASSET_ROLES = (
    ('CLOTHING', 'Clothing', 'Clothes that receive body weights'),
    ('STOCKINGS', 'Stockings', 'Fitted stockings that receive body weights'),
    ('SHOES', 'Shoes', 'Footwear; automatic weights provide a starting point'),
    ('HAIR', 'Hair', 'Hair mesh for the Hair workflow'),
    ('SKIRT', 'Skirt', 'Skirt mesh for its dedicated rig workflow'),
)

BINDING_METHODS = (
    ('TRANSFER', 'Surface Transfer',
     'Copy body weights with Nearest Face Interpolated; useful for surfaces close to the body'),
    ('AUTO', 'Automatic Weights',
     'Calculate Blender bone heat weights as a starting point for hand painting'),
)


def _mesh_only(_self, obj):
    return obj.type == 'MESH'


def _rig_only(_self, obj):
    return obj.type == 'ARMATURE'


def settings(context):
    return getattr(context.scene, 'character_designer_setup', None)


def preferred_rig(context, override=None):
    state = settings(context)
    return override if override is not None else state.rig if state else None


BONE_ROLES = (
    ('HIPS', 'Hips', 'The central bone that moves the whole pelvis'),
    ('HEAD', 'Head', 'The head bone used to attach hair'),
)


def _bone_role(role):
    role = role.upper()
    if role == 'PELVIS':
        role = 'HIPS'
    if role not in {'HIPS', 'HEAD'}:
        raise ValueError('Unknown character bone role')
    return role


def _mapping(state, armature, *, create=False):
    if state is None or armature is None:
        return None
    entry = next((item for item in state.bone_mappings if item.rig == armature), None)
    if entry is None and create:
        entry = state.bone_mappings.add()
        entry.rig = armature
    return entry


def footwear_reference(context, armature=None):
    """One optional visual-fit reference per character; legacy assets are scoped by rig."""
    armature = preferred_rig(context, armature)
    state = settings(context)
    entry = _mapping(state, armature)
    if entry is not None and entry.footwear is not None:
        shoe = entry.footwear
        if shoe.name not in context.scene.objects or shoe.type != 'MESH':
            raise ValueError('The saved footwear reference is not a mesh in this scene; choose it again.')
        return shoe
    if state is None or armature is None:
        return None
    candidates = {item.object for item in state.assets if item.role == 'SHOES'
                  and item.object is not None and item.object.type == 'MESH'
                  and item.object.name in context.scene.objects
                  and any(mod.type == 'ARMATURE' and mod.object == armature for mod in item.object.modifiers)}
    return next(iter(candidates)) if len(candidates) == 1 else None


def _bone_name(name):
    return name.rsplit(':', 1)[-1].casefold().removeprefix('def-')


def bone_candidates(armature, role):
    """Read-only central-bone detection; side names and Root are never guesses."""
    role = _bone_role(role)
    if armature is None or armature.type != 'ARMATURE':
        return ()
    aliases = {'hips', 'hip', 'pelvis'} if role == 'HIPS' else {'head'}
    names = tuple(bone.name for bone in armature.data.bones if _bone_name(bone.name) in aliases)
    if names or role != 'HEAD':
        return names
    # Some generated rigs call Head spine.006. Preserve the existing anatomical
    # detection only when each eye is unique and both share the same parent.
    left = [bone for bone in armature.data.bones
            if _bone_name(bone.name) in {'eye.l', 'eye_l', 'l_eye', 'left_eye'}]
    right = [bone for bone in armature.data.bones
             if _bone_name(bone.name) in {'eye.r', 'eye_r', 'r_eye', 'right_eye'}]
    if (len(left) == len(right) == 1 and left[0].parent is not None
            and left[0].parent == right[0].parent):
        return (left[0].parent.name,)
    return ()


def bone_mapping_status(context, role, armature=None, override=''):
    """Describe the exact resolved target without changing saved user choices."""
    role = _bone_role(role)
    armature = preferred_rig(context, armature)
    label = 'Hips' if role == 'HIPS' else 'Head'
    result = {'armature': armature, 'role': role, 'name': '', 'requested': '',
              'status': 'NO_RIG', 'candidates': (), 'message': 'Set Main Rig in Character Setup.'}
    if armature is None or armature.type != 'ARMATURE':
        return result
    entry = _mapping(settings(context), armature)
    requested = override or (getattr(entry, role.lower() + '_bone') if entry else '')
    if requested:
        result['requested'] = requested
        if requested not in armature.data.bones:
            result.update(status='INVALID', message=f'{label} bone "{requested}" is missing from {armature.name}; choose it again.')
        else:
            result.update(name=requested, status='OVERRIDE' if override else 'CONFIRMED', message='')
        return result
    candidates = bone_candidates(armature, role)
    result['candidates'] = candidates
    if len(candidates) == 1:
        result.update(name=candidates[0], status='AUTO', message='')
    elif candidates:
        result.update(status='AMBIGUOUS', message=f'Multiple {label} candidates; choose one in Character Setup.')
    else:
        result.update(status='MISSING', message=f'Choose {label} in Character Setup.')
    return result


def resolve_bone(context, role, armature=None, override=''):
    status = bone_mapping_status(context, role, armature, override)
    if not status['name']:
        raise ValueError(status['message'])
    return status['name']


def _get_mapping_field(state, role):
    armature = state.rig
    entry = _mapping(state, armature)
    confirmed = getattr(entry, role.lower() + '_bone') if entry else ''
    if confirmed:
        return confirmed
    names = bone_candidates(armature, role)
    return names[0] if len(names) == 1 else ''


def _set_mapping_field(state, role, value):
    if state.rig is None:
        return
    entry = _mapping(state, state.rig, create=True)
    setattr(entry, role.lower() + '_bone', value)


def selected_character_bone(context):
    armature = context.active_object
    if armature is None or armature.type != 'ARMATURE':
        return None
    # Only object ownership excludes a generated accessory rig. A main rig may
    # contain generated hair bones or the artist's own non-deform controls.
    if any(armature.get(key) for key in (
            'character_designer_skirt_owner', 'character_designer_hair_bones_owner',
            'character_designer_hair_variant_version')):
        return None
    if context.mode == 'EDIT_ARMATURE':
        bone = armature.data.edit_bones.active
    elif context.mode == 'POSE':
        bone = context.active_pose_bone
    else:
        return None
    selected = (getattr(bone, 'select', getattr(getattr(bone, 'bone', None), 'select', False))
                if bone is not None else False)
    return (armature, bone.name) if selected else None


def _selected_mesh(context):
    obj = context.active_object
    return obj if (obj is not None and obj.type == 'MESH'
                   and obj.select_get(view_layer=context.view_layer)) else None


def quick_bind_target(context):
    """The selected ordinary mesh; custom hair/skirt bindings stay in their pages."""
    state = settings(context)
    obj = _selected_mesh(context)
    if state is None or obj is None or obj == state.body:
        return None
    if any(item.object == obj and item.role in {'HAIR', 'SKIRT'} for item in state.assets):
        return None
    return obj


def role_source(context, role):
    state = settings(context)
    if state is None:
        return None
    members = [item.object for item in state.assets
               if item.role == role and item.object is not None
               and item.object.name in context.scene.objects]
    if context.active_object in members:
        return context.active_object
    if 0 <= state.active_asset < len(state.assets):
        item = state.assets[state.active_asset]
        if item.role == role and item.object in members:
            return item.object
    return members[0] if len(members) == 1 else None


def remember_asset(context, obj, role):
    state = settings(context)
    if state is None or obj is None or obj.type != 'MESH':
        return
    if role not in {entry[0] for entry in ASSET_ROLES}:
        raise ValueError('Unknown character asset role')
    index = next((i for i, item in enumerate(state.assets) if item.object == obj), None)
    if index is None:
        index = len(state.assets)
        item = state.assets.add()
        item.object = obj
    else:
        item = state.assets[index]
    item.role = role
    state.active_asset = index


class CharacterDesignerAsset(PropertyGroup):
    object: PointerProperty(type=bpy.types.Object, name='Mesh', poll=_mesh_only)
    role: EnumProperty(name='Role', items=ASSET_ROLES)


class CharacterDesignerBoneMapping(PropertyGroup):
    rig: PointerProperty(type=bpy.types.Object, poll=_rig_only)
    hips_bone: StringProperty()
    head_bone: StringProperty()
    footwear: PointerProperty(type=bpy.types.Object, name='Footwear', poll=_mesh_only,
                             description='Optional shoe mesh used to place foot controls outside the heel; saved for this Main Rig')


class CharacterDesignerSetup(PropertyGroup):
    rig: PointerProperty(type=bpy.types.Object, name='Main Rig', poll=_rig_only,
                         description='Saved character armature used by weight, hair and skirt tools')
    body: PointerProperty(type=bpy.types.Object, name='Body Weight Source', poll=_mesh_only,
                          description='Already weighted body mesh from which clothes receive deform weights')
    bone_mappings: CollectionProperty(type=CharacterDesignerBoneMapping)
    hips_bone: StringProperty(
        name='Hips', description='Central pelvis attachment shared by rig tools; clear to detect automatically',
        get=lambda self: _get_mapping_field(self, 'HIPS'),
        set=lambda self, value: _set_mapping_field(self, 'HIPS', value),
    )
    head_bone: StringProperty(
        name='Head', description='Hair attachment shared by rig tools; clear to detect automatically',
        get=lambda self: _get_mapping_field(self, 'HEAD'),
        set=lambda self, value: _set_mapping_field(self, 'HEAD', value),
    )
    assets: CollectionProperty(type=CharacterDesignerAsset)
    active_asset: IntProperty(default=-1)
    add_role: EnumProperty(name='Role', items=ASSET_ROLES)
    binding_method: EnumProperty(name='Method', items=BINDING_METHODS, default='TRANSFER')
    last_message: StringProperty(options={'SKIP_SAVE'})


class CHARACTERDESIGNER_OT_capture_character_bone(Operator):
    bl_idname = 'character_designer.capture_character_bone'
    bl_label = 'Use Selected Bone'
    bl_description = 'Use the active selected bone as this character role and remember its armature as Main Rig'
    bl_options = {'REGISTER', 'UNDO'}
    role: EnumProperty(items=BONE_ROLES, default='HIPS')

    @classmethod
    def poll(cls, context):
        return selected_character_bone(context) is not None and settings(context) is not None

    def execute(self, context):
        selected = selected_character_bone(context)
        if selected is None:
            self.report({'ERROR'}, 'Select a bone in Pose Mode or Armature Edit Mode.')
            return {'CANCELLED'}
        armature, name = selected
        state = settings(context)
        state.rig = armature
        setattr(state, self.role.lower() + '_bone', name)
        self.report({'INFO'}, f'{self.role.title()}: {armature.name} / {name}')
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_register_assets(Operator):
    bl_idname = 'character_designer.register_assets'
    bl_label = 'Remember Selected Meshes'
    bl_description = 'Save the selected meshes and their role with this blend file; no weights are changed'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        state = settings(context)
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                remember_asset(context, obj, state.add_role)
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_character_asset(Operator):
    bl_idname = 'character_designer.character_asset'
    bl_label = 'Character Mesh'
    bl_description = 'Select a saved mesh, or remove only its saved reference'
    bl_options = {'REGISTER', 'UNDO'}
    index: IntProperty(default=-1)
    action: EnumProperty(items=(('SELECT', 'Select', ''), ('FORGET', 'Forget', '')))

    def execute(self, context):
        state = settings(context)
        if not 0 <= self.index < len(state.assets):
            return {'CANCELLED'}
        if self.action == 'FORGET':
            state.assets.remove(self.index)
            state.active_asset = min(state.active_asset, len(state.assets) - 1)
            return {'FINISHED'}
        obj = state.assets[self.index].object
        if obj is None or obj.name not in context.view_layer.objects:
            self.report({'ERROR'}, 'This mesh is not available in the current view layer')
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, 'Return to Object Mode to select a saved mesh')
            return {'CANCELLED'}
        if not obj.visible_get(view_layer=context.view_layer) or obj.hide_select:
            self.report({'ERROR'}, 'Unhide and unlock this mesh before selecting it')
            return {'CANCELLED'}
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        state.active_asset = self.index
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_quick_bind(Operator):
    bl_idname = 'character_designer.quick_bind'
    bl_label = 'Bind Weights'
    bl_description = 'Bind the active mesh to Main Rig; keep the original weights for Restore Previous Binding'
    bl_options = {'REGISTER', 'UNDO'}
    mode: EnumProperty(items=BINDING_METHODS, default='TRANSFER')

    @classmethod
    def poll(cls, context):
        return (context.mode == 'OBJECT' and quick_bind_target(context) is not None
                and preferred_rig(context) is not None)

    def execute(self, context):
        from .quick_bind import bind_weights
        state = settings(context)
        target = context.active_object
        if target == state.body:
            self.report({'ERROR'}, 'Select a clothing or shoe mesh; the saved body is the weight source')
            return {'CANCELLED'}
        if any(item.object == target and item.role in {'HAIR', 'SKIRT'} for item in state.assets):
            self.report({'ERROR'}, 'Use the dedicated Hair or Skirt binding for this registered mesh')
            return {'CANCELLED'}
        try:
            result = bind_weights(context, target, state.rig, body=state.body, mode=self.mode)
        except (ValueError, RuntimeError) as exc:
            state.last_message = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        state.last_message = f'{target.name}: weights bound to {state.rig.name}'
        self.report({'INFO'}, state.last_message)
        return {'FINISHED'}


class CHARACTERDESIGNER_PT_character_setup(Panel):
    bl_label = 'Character Setup'
    bl_idname = 'CHARACTERDESIGNER_PT_character_setup'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = -10

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) in {UI_PAGE_WEIGHT, UI_PAGE_RIG}

    def draw(self, context):
        layout = self.layout
        state = settings(context)
        layout.prop(state, 'rig')
        layout.prop(state, 'body')
        for role, label, _description in BONE_ROLES:
            row = layout.row(align=True)
            field = role.lower() + '_bone'
            if state.rig is not None:
                row.prop_search(state, field, state.rig.data, 'bones', text=label)
            else:
                field_row = row.row()
                field_row.enabled = False
                field_row.prop(state, field, text=label)
            row.operator('character_designer.capture_character_bone', text='', icon='EYEDROPPER').role = role
            status = bone_mapping_status(context, role)
            if status['status'] in {'INVALID', 'AMBIGUOUS', 'MISSING'}:
                layout.label(text=status['message'], icon='ERROR' if status['status'] == 'INVALID' else 'INFO')


class CHARACTERDESIGNER_OT_restore_quick_binding(Operator):
    bl_idname = 'character_designer.restore_quick_binding'
    bl_label = 'Restore Previous Binding'
    bl_description = 'Restore weights and binding from before the first Quick Bind; an originally unbound mesh returns to its unbound state'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from .quick_bind import has_binding_backup
        return context.mode == 'OBJECT' and has_binding_backup(_selected_mesh(context))

    def execute(self, context):
        from .quick_bind import restore_binding
        try:
            restore_binding(context, context.active_object)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        settings(context).last_message = f'{context.active_object.name}: previous binding restored'
        self.report({'INFO'}, settings(context).last_message)
        return {'FINISHED'}


class CHARACTERDESIGNER_PT_quick_bind(Panel):
    bl_label = 'Quick Bind'
    bl_idname = 'CHARACTERDESIGNER_PT_quick_bind'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = SIDEBAR_CATEGORY
    bl_order = -9

    @classmethod
    def poll(cls, context):
        from .quick_bind import has_binding_backup
        return active_ui_page(context) == UI_PAGE_WEIGHT and (
            quick_bind_target(context) is not None
            or has_binding_backup(_selected_mesh(context))
        )

    def draw(self, context):
        layout = self.layout
        state = settings(context)
        target = context.active_object
        layout.label(text=target.name, icon='OUTLINER_OB_MESH')
        if quick_bind_target(context) is not None:
            layout.prop(state, 'binding_method')
            transfer = state.binding_method == 'TRANSFER'
            if transfer:
                layout.label(text='Nearest Face Interpolated')
            if context.mode != 'OBJECT':
                layout.label(text='Return to Object Mode to bind.', icon='INFO')
            elif state.rig is None:
                layout.label(text='Set Main Rig in Character Setup.', icon='INFO')
            elif transfer and state.body is None:
                layout.label(text='Set Body Weight Source above.', icon='INFO')
            row = layout.row()
            row.alert = True
            row.enabled = not transfer or state.body is not None
            row.operator('character_designer.quick_bind',
                         icon='MOD_DATA_TRANSFER' if transfer else 'ARMATURE_DATA').mode = state.binding_method
        from .quick_bind import has_binding_backup
        if has_binding_backup(target):
            row = layout.row()
            row.alert = True
            row.operator('character_designer.restore_quick_binding', icon='LOOP_BACK')


CHARACTER_SETUP_CLASSES = (
    CharacterDesignerAsset, CharacterDesignerBoneMapping, CharacterDesignerSetup,
    CHARACTERDESIGNER_OT_capture_character_bone,
    CHARACTERDESIGNER_OT_register_assets, CHARACTERDESIGNER_OT_character_asset,
    CHARACTERDESIGNER_OT_quick_bind, CHARACTERDESIGNER_OT_restore_quick_binding,
    CHARACTERDESIGNER_PT_character_setup,
    CHARACTERDESIGNER_PT_quick_bind,
)
