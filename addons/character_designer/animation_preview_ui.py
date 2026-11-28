"""Compare an original Action with a copy edited using Blender's native tools."""
import bpy
from bpy.props import EnumProperty, FloatProperty

from . import animation_cleanup as cleanup
from . import animation_preview_session as session


OPERATIONS = (
    ('START', 'Compare Key Edits', 'Create an editable copy for Graph Editor tools such as Decimate'),
    ('ORIGINAL', 'Original', 'Show the original motion at the current frame'),
    ('REDUCED', 'Edited', 'Show the editable copy at the current frame'),
    ('UPDATE', 'Rebuild Preview', 'Rebuild the temporary Action from the original'),
    ('APPLY', 'Apply Edits', 'Apply the edited copy to the original Action'),
    ('DISCARD', 'Discard', 'Keep Original and discard the edited copy'),
)


def _redraw_editors(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}:
                area.tag_redraw()


class CHARACTERDESIGNER_OT_clean_animation_data(bpy.types.Operator):
    bl_idname = 'character_designer.clean_animation_data'
    bl_label = 'Clean Animation Data'
    bl_description = 'Remove redundant neutral transform curves from the entire active prepared clip'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return cleanup.prepared_binding(context) is not None

    def execute(self, context):
        binding = cleanup.prepared_binding(context)
        if binding is None:
            return {'CANCELLED'}
        rig, action, handle = binding
        try:
            session._ready(context, rig)
            session._writable_source(rig, action, handle)
            result = cleanup.clean(rig, action, handle, published=True)
            hidden = cleanup.hide_unused_scale(rig, action, handle)
        except (RuntimeError, ValueError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _redraw_editors(context)
        message = f"Removed {result['curves']:,} redundant curves and {result['keys']:,} keys"
        if hidden:
            message += f" · Hid {hidden:,} unused Scale curves"
        self.report({'INFO'}, message)
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_hide_unused_scale(bpy.types.Operator):
    bl_idname = 'character_designer.hide_unused_scale'
    bl_label = 'Hide Unused Scale Curves'
    bl_description = 'Hide unused scale axes of selected controls in the Graph Editor'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        rig = context.active_object
        return bool(context.mode == 'POSE' and rig and rig.type == 'ARMATURE' and
                    session.SESSION not in rig and rig.animation_data and rig.animation_data.action
                    and context.selected_pose_bones)

    def execute(self, context):
        try:
            rig = session._rig(context)
            session._ready(context, rig)
            action, handle = session._binding(rig)
            session._writable_source(rig, action, handle)
            count = cleanup.hide_unused_scale(rig, action, handle,
                                              bone_names=(bone.name for bone in context.selected_pose_bones))
        except (ValueError, RuntimeError, ReferenceError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        _redraw_editors(context)
        self.report({'INFO'}, f'Hid {count:,} unused scale curves')
        return {'FINISHED'} if count else {'CANCELLED'}


class CHARACTERDESIGNER_OT_key_reduction_preview(bpy.types.Operator):
    bl_idname = 'character_designer.key_reduction_preview'
    bl_label = 'Compare Key Edits'
    bl_options = {'UNDO'}

    operation: EnumProperty(items=OPERATIONS, options={'HIDDEN'})
    position_mm: FloatProperty(default=2.0, min=0.0, options={'HIDDEN', 'SKIP_SAVE'})
    rotation_degrees: FloatProperty(default=1.0, min=0.0, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, properties):
        return next(detail for key, _, detail in OPERATIONS if key == properties.operation)

    def invoke(self, context, event):
        if self.operation == 'APPLY':
            state = session.status(context)
            if state is None:
                return {'CANCELLED'}
            message = _key_summary(state) or 'Apply the edited copy to the original Action.'
            changed_cycles = session.changed_cycle_ranges(context)
            if changed_cycles == 1:
                message += '\nA repeating curve changed its frame range. Check the loop seam.'
            elif changed_cycles > 1:
                message += f'\n{changed_cycles} repeating curves changed frame range. Check the loop seam.'
            return context.window_manager.invoke_confirm(
                self, event, title='Apply Key Edits?', confirm_text='Apply Edits', message=message)
        return self.execute(context)

    def execute(self, context):
        try:
            if self.operation in {'START', 'UPDATE'}:
                position = self.position_mm if self.properties.is_property_set('position_mm') else None
                rotation = self.rotation_degrees if self.properties.is_property_set('rotation_degrees') else None
                action = session.start if self.operation == 'START' else session.regenerate
                action(context, position, rotation)
            elif self.operation in {'ORIGINAL', 'REDUCED'}:
                session.switch(context, self.operation)
            elif self.operation == 'APPLY':
                session.apply(context)
            else:
                session.discard(context)
        except (ValueError, RuntimeError, ReferenceError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        _redraw_editors(context)
        return {'FINISHED'}


def _button(row, operation, label, **kwargs):
    row.operator(CHARACTERDESIGNER_OT_key_reduction_preview.bl_idname,
                 text=label, **kwargs).operation = operation


def _key_summary(state):
    if 'before' not in state:
        return None
    before, after = state['before'], state['after']
    label = 'Selected keys'
    if state.get('edit_only'):
        if not (state['removed'] or state['added']):
            return None
        before, after = before - state['same_count'], after - state['same_count']
        label = 'Keys on changed curves'
    summary = f'{label}  {before:,} → {after:,}'
    if before:
        delta = after - before
        sign = '−' if delta < 0 else '+' if delta > 0 else ''
        summary += f'  ({sign}{abs(delta) / before:.1%})'
    return summary


def draw(layout, context, state):
    if state is None:
        if cleanup.prepared_binding(context):
            layout.operator(CHARACTERDESIGNER_OT_clean_animation_data.bl_idname, icon='BRUSH_DATA')
        rig = context.object
        if (context.mode == 'POSE' and rig is not None and rig.animation_data
                and rig.animation_data.action):
            row = layout.row(align=True)
            _button(row, 'START', 'Compare Key Edits', icon='KEYFRAME_HLT')
            if context.selected_pose_bones:
                row.operator(CHARACTERDESIGNER_OT_hide_unused_scale.bl_idname, text='', icon='HIDE_ON')
        return

    box = layout.box()
    if summary := _key_summary(state):
        box.label(text=summary)
    row = box.row(align=True)
    for view, label in (('ORIGINAL', 'Original'), ('REDUCED', 'Edited')):
        _button(row, view, label, depress=state['view'] == view)
    row = box.row(align=True)
    row.operator_context = 'INVOKE_DEFAULT'
    if state['view'] == 'ORIGINAL':
        _button(row, 'DISCARD', 'Keep Original', icon='CHECKMARK')
    elif state['view'] == 'REDUCED':
        _button(row, 'APPLY', 'Apply Edits', icon='CHECKMARK')
        _button(row, 'DISCARD', 'Discard', icon='X')
    else:
        _button(row, 'DISCARD', 'Discard', icon='X')
