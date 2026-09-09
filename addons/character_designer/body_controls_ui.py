"""Compact whole-body and limb-display actions in the existing Body panel."""
import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from . import limb_ik, root_control, control_colors


class CHARACTERDESIGNER_OT_root_control(Operator):
    bl_idname = 'character_designer.root_control'
    bl_label = 'Whole Body Root'
    bl_description = 'Move the character and its IK controls together; keep the native skeleton'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('BUILD', 'Add Root', ''), ('SELECT', 'Select Root', ''),
                                ('REMOVE', 'Remove Root', 'Preserve the current pose')))

    @classmethod
    def poll(cls, context):
        return (context.object is not None and context.object.type == 'ARMATURE'
                and context.mode in {'OBJECT', 'POSE'})

    def execute(self, context):
        rig = context.object
        try:
            if self.action == 'REMOVE':
                root_control.remove(context, rig)
                control_colors.cleanup(rig)
            else:
                if self.action == 'BUILD':
                    root_control.build(context, rig)
                    control_colors.sync(rig)
                limb_ik._validate_inventory(rig)
                name = root_control.control_name(rig)
                if not name:
                    raise ValueError('Add the whole-body Root first.')
                limb_ik._mode_set(context, rig, 'POSE')
                for pb in rig.pose.bones:
                    pb.select = pb.name == name
                rig.data.bones.active = rig.data.bones[name]
                rig.data.bones[name].hide = False
                # Respect the existing collection layout and show its root input.
                for collection in rig.data.bones[name].collections:
                    if collection.name == 'Animation':
                        collection.is_visible = True
            self.report({'INFO'}, 'Root removed; current pose preserved.' if self.action == 'REMOVE'
                        else 'Root controls the whole character. Move G; rotate R.')
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_limb_fk_visuals(Operator):
    bl_idname = 'character_designer.limb_fk_visuals'
    bl_label = 'Limb Control Shapes'
    bl_description = 'Fit reversible FK rings and IK display sizes without changing bone poses or weights'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('BUILD', 'Add FK Rings', ''), ('REMOVE', 'Remove FK Rings', ''),
                                ('FIT_IK', 'Fit IK Sizes', ''), ('RESTORE_IK', 'Restore IK Sizes', '')))

    @classmethod
    def poll(cls, context):
        return CHARACTERDESIGNER_OT_root_control.poll(context)

    def execute(self, context):
        from . import limb_fk_visuals
        rig = context.object
        try:
            if self.action == 'BUILD':
                limb_fk_visuals.build(context, rig)
                control_colors.sync(rig)
            elif self.action == 'REMOVE':
                limb_fk_visuals.remove(context, rig)
                control_colors.cleanup(rig)
            elif self.action == 'FIT_IK':
                limb_fk_visuals.fit_ik_sizes(context, rig)
            else:
                limb_fk_visuals.restore_ik_sizes(context, rig)
            self.report({'INFO'}, 'Control shapes updated; bone poses and weights preserved.')
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_root_controls(layout, context):
    rig = context.object
    if rig is None or rig.type != 'ARMATURE':
        return
    box = layout.box()
    try:
        name = root_control.control_name(rig)
        row = box.row(align=True)
        if name:
            row.operator('character_designer.root_control', text='Root · Whole Body', icon='PIVOT_CURSOR').action = 'SELECT'
            if root_control.get_record(rig):
                removal = row.row(align=True)
                removal.alert = True
                removal.operator('character_designer.root_control', text='', icon='TRASH').action = 'REMOVE'
                box.prop(rig.pose.bones[name], '["' + root_control.SCALE_PROPERTY + '"]', text='Root Scale')
        else:
            row.alert = True
            row.operator('character_designer.root_control', text='Add Whole Body Root', icon='PIVOT_CURSOR').action = 'BUILD'
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        box.label(text=str(exc), icon='ERROR')


def draw_fk_visuals(layout, context):
    from . import limb_fk_visuals
    rig = context.object
    if rig is None or rig.type != 'ARMATURE':
        return
    row = layout.row(align=True)
    if limb_fk_visuals.get_record(rig):
        row.operator('character_designer.limb_fk_visuals', text='Remove FK Rings', icon='LOOP_BACK').action = 'REMOVE'
    else:
        row.operator('character_designer.limb_fk_visuals', text='Add FK Rings', icon='MESH_CIRCLE').action = 'BUILD'
    row.operator('character_designer.limb_fk_visuals', text='Fit IK Sizes', icon='FULLSCREEN_EXIT').action = 'FIT_IK'
    # The fit action is explicit; artist-authored display edits are never overwritten automatically.
    if limb_fk_visuals.has_ik_size_backup(rig):
        layout.operator('character_designer.limb_fk_visuals', text='Restore IK Sizes', icon='LOOP_BACK').action = 'RESTORE_IK'


class CHARACTERDESIGNER_OT_head_neck_visuals(Operator):
    bl_idname = 'character_designer.head_neck_visuals'
    bl_label = 'Head and Neck Controls'
    bl_description = 'Fit an open head frame and neck collar; preserve native pivots, poses and weights'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('BUILD', 'Add Head / Neck', ''),
                                ('SELECT_HEAD', 'Select Head', ''),
                                ('SELECT_NECK', 'Select Neck', ''),
                                ('REMOVE', 'Restore Head / Neck Shapes', 'Restore the display saved before adding these controls')))

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == 'ARMATURE' and context.mode in {'OBJECT', 'POSE'}
                    and obj.is_editable and not obj.library and not obj.data.library)

    def execute(self, context):
        from . import head_neck_visuals
        rig = context.object
        try:
            if self.action == 'REMOVE':
                head_neck_visuals.remove(context, rig)
                control_colors.cleanup(rig)
                self.report({'INFO'}, 'Original head and neck shapes restored; pose unchanged.')
                return {'FINISHED'}
            if self.action == 'BUILD':
                record = head_neck_visuals.build(context, rig)
                control_colors.sync(rig)
            else:
                record = head_neck_visuals.validate(rig)
            if not record:
                raise ValueError('Add Head / Neck controls first.')
            role = 'NECK' if self.action == 'SELECT_NECK' else 'HEAD'
            name = next((name for name, binding in record['bindings'].items()
                         if binding['role'] == role), None)
            if name is None:
                raise ValueError('No neck control was created for this head mapping.')
            limb_ik._mode_set(context, rig, 'POSE')
            for pb in rig.pose.bones:
                pb.select = pb.name == name
            bone = rig.data.bones[name]
            rig.data.bones.active = bone
            bone.hide = False
            # Use the current grouping. Showing one control does not rebuild collections.
            collections = list(bone.collections)
            if not any(c.is_visible_effectively for c in collections):
                preferred = next((c for c in collections if c.name == 'Animation'),
                                 collections[0] if collections else None)
                if preferred is not None:
                    preferred.is_visible = True
                    while preferred.parent:
                        preferred = preferred.parent
                        preferred.is_visible = True
            self.report({'INFO'}, f'{role.title()}: rotate with R around the original bone pivot.')
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_head_neck_visuals(layout, context):
    from . import head_neck_visuals
    rig = context.object
    if rig is None or rig.type != 'ARMATURE':
        return
    row = layout.row(align=True)
    try:
        record = head_neck_visuals.get_record(rig)
        if record:
            for role, label in (('HEAD', 'Head'), ('NECK', 'Neck')):
                if any(binding['role'] == role for binding in record['bindings'].values()):
                    row.operator('character_designer.head_neck_visuals', text=label,
                                 icon='BONE_DATA').action = 'SELECT_' + role
            restore = row.row(align=True)
            restore.alert = True
            restore.operator('character_designer.head_neck_visuals', text='', icon='LOOP_BACK').action = 'REMOVE'
        else:
            row.alert = True
            row.operator('character_designer.head_neck_visuals', text='Add Head / Neck',
                         icon='BONE_DATA').action = 'BUILD'
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        row.label(text=str(exc), icon='ERROR')


class CHARACTERDESIGNER_OT_body_detail_visuals(Operator):
    bl_idname = 'character_designer.body_detail_visuals'
    bl_label = 'Breast and Hips Controls'
    bl_description = 'Fit simple breast and pelvis rings; preserve native pivots, poses and weights'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=(('BUILD', 'Add Breasts / Hips', ''),
                                ('SELECT_BREAST_L', 'Select Left Breast', ''),
                                ('SELECT_BREAST_R', 'Select Right Breast', ''),
                                ('SELECT_HIPS', 'Select Hips', ''),
                                ('REMOVE', 'Restore Original Shapes', 'Restore the displays and colors saved before adding these rings')))
    hips_name: StringProperty(name='Hips', description='Central pelvis bone; blank uses Character Setup')
    left_name: StringProperty(name='Left Breast', description='Existing left breast bone')
    right_name: StringProperty(name='Right Breast', description='Existing right breast bone')

    @classmethod
    def poll(cls, context):
        return CHARACTERDESIGNER_OT_head_neck_visuals.poll(context)

    def invoke(self, context, event):
        from . import body_detail_visuals
        if self.action == 'BUILD' and not body_detail_visuals.get_record(context.object):
            try:
                body_detail_visuals.resolve_bones(context, context.object,
                    hips_name=self.hips_name or None, left_name=self.left_name or None,
                    right_name=self.right_name or None)
            except (ValueError, RuntimeError):
                return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text='Choose the three existing bones.', icon='BONE_DATA')
        for field in ('hips_name', 'left_name', 'right_name'):
            layout.prop_search(self, field, context.object.data, 'bones')

    def execute(self, context):
        from . import body_detail_visuals
        rig = context.object
        try:
            if self.action == 'REMOVE':
                body_detail_visuals.remove(context, rig)
                control_colors.cleanup(rig)
                self.report({'INFO'}, 'Original breast and hips displays restored; pose unchanged.')
                return {'FINISHED'}
            if self.action == 'BUILD':
                record = body_detail_visuals.build(context, rig, hips_name=self.hips_name or None,
                    left_name=self.left_name or None, right_name=self.right_name or None)
                control_colors.sync(rig)
            else:
                record = body_detail_visuals.validate(rig)
            if not record:
                raise ValueError('Add Breasts / Hips controls first.')
            role = 'HIPS' if self.action == 'BUILD' else self.action.removeprefix('SELECT_')
            name = next(name for name, entry in record['bindings'].items() if entry['role'] == role)
            limb_ik._mode_set(context, rig, 'POSE')
            for pb in rig.pose.bones:
                pb.select = pb.name == name
            bone = rig.data.bones[name]
            rig.data.bones.active = bone
            bone.hide = False
            collections = list(bone.collections)
            if not any(c.is_visible_effectively for c in collections):
                collection = next((c for c in collections if c.name == 'Animation'),
                                  collections[0] if collections else None)
                while collection is not None:
                    collection.is_visible = True
                    collection = collection.parent
            self.report({'INFO'}, f'{name}: move or rotate the ring using its original bone pivot.')
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError, StopIteration) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_body_detail_visuals(layout, context):
    from . import body_detail_visuals
    rig = context.object
    if rig is None or rig.type != 'ARMATURE':
        return
    row = layout.row(align=True)
    try:
        record = body_detail_visuals.get_record(rig)
        if record:
            for role, label in (('BREAST_L', 'Breast L'), ('BREAST_R', 'Breast R'), ('HIPS', 'Hips')):
                row.operator('character_designer.body_detail_visuals', text=label,
                             icon='MESH_CIRCLE').action = 'SELECT_' + role
            restore = row.row(align=True)
            restore.alert = True
            restore.operator('character_designer.body_detail_visuals', text='', icon='LOOP_BACK').action = 'REMOVE'
        else:
            row.alert = True
            row.operator('character_designer.body_detail_visuals', text='Add Breasts / Hips',
                         icon='MESH_CIRCLE').action = 'BUILD'
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        row.label(text=str(exc), icon='ERROR')


BODY_CONTROL_UI_CLASSES = (CHARACTERDESIGNER_OT_root_control, CHARACTERDESIGNER_OT_limb_fk_visuals,
                           CHARACTERDESIGNER_OT_head_neck_visuals, CHARACTERDESIGNER_OT_body_detail_visuals)
