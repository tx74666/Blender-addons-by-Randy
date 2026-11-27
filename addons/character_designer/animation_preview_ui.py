"""Compare selected controls before and after key reduction in one viewport."""
import bpy
from bpy.props import EnumProperty, FloatProperty

from . import animation_cleanup as cleanup
from . import animation_preview_session as session


OPERATIONS = (
    ('START', 'Preview Key Reduction', 'Simplify selected controls on a temporary copy'),
    ('ORIGINAL', 'Original', 'Show the original motion at the current frame'),
    ('REDUCED', 'Reduced', 'Edit the reduced copy or compare it with Original at the current frame'),
    ('UPDATE', 'Adjust Reduction', 'Rebuild Reduced from Original, replacing edits made to the preview'),
    ('APPLY', 'Apply', 'Apply Reduced to the current animation, including your curve edits'),
    ('DISCARD', 'Discard', 'End the comparison and discard the preview'),
)


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
        except (RuntimeError, ValueError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Removed {result['curves']:,} redundant curves and {result['keys']:,} keys")
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_key_reduction_preview(bpy.types.Operator):
    bl_idname = 'character_designer.key_reduction_preview'
    bl_label = 'Key Reduction Preview'
    bl_options = {'UNDO'}

    operation: EnumProperty(items=OPERATIONS, options={'HIDDEN'})
    position_mm: FloatProperty(name='Position (mm)', default=2.0, min=0.0, precision=2,
                               description='Allowed local position error, in millimeters')
    rotation_degrees: FloatProperty(name='Rotation (degrees)', default=1.0, min=0.0, precision=2,
                                    description='Allowed rotation error, in degrees')

    @classmethod
    def description(cls, _context, properties):
        return next(detail for key, _, detail in OPERATIONS if key == properties.operation)

    def invoke(self, context, event):
        if self.operation == 'APPLY':
            state = session.status(context)
            if state is None:
                return {'CANCELLED'}
            message = 'Apply the current Reduced preview.'
            if 'before' in state:
                message = f"Selected transform keys  {state['before']:,} → {state['after']:,}"
            return context.window_manager.invoke_confirm(
                self, event, title='Apply Key Reduction?', confirm_text='Apply Reduction', message=message)
        if self.operation == 'UPDATE':
            state = session.status(context)
            if state is None:
                return {'CANCELLED'}
            self.position_mm = state['position_mm']
            self.rotation_degrees = state['rotation_degrees']
            return context.window_manager.invoke_props_dialog(self, width=285, title='Adjust Reduction')
        return self.execute(context)

    def draw(self, context):
        self.layout.prop(self, 'position_mm')
        self.layout.prop(self, 'rotation_degrees')
        self.layout.label(text='Rebuilding from Original replaces edits in Reduced.')
        state = session.status(context)
        if state and 'before' in state:
            self.layout.label(text=f"Selected total  {state['before']:,} → {state['after']:,}")
            if state['same_count']:
                self.layout.label(text=f"Same key count  {state['same_count']:,}")
            if state['added']:
                self.layout.label(text=f"Added keys  {state['added']:,}")
        if state and state['skipped']:
            self.layout.label(text='Curves skipped when building preview')
            for reason, count in sorted(state['skipped'].items()):
                self.layout.label(text=f'{count:,} curves · {reason}')

    def execute(self, context):
        try:
            if self.operation == 'START':
                session.start(context, self.position_mm, self.rotation_degrees)
            elif self.operation == 'UPDATE':
                session.regenerate(context, self.position_mm, self.rotation_degrees)
            elif self.operation in {'ORIGINAL', 'REDUCED'}:
                session.switch(context, self.operation)
            elif self.operation == 'APPLY':
                session.apply(context)
            else:
                session.discard(context)
        except (ValueError, RuntimeError, ReferenceError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}:
                    area.tag_redraw()
        return {'FINISHED'}


def _button(row, operation, label, **kwargs):
    row.operator(CHARACTERDESIGNER_OT_key_reduction_preview.bl_idname,
                 text=label, **kwargs).operation = operation


def draw(layout, context, state):
    if state is None:
        if cleanup.prepared_binding(context):
            layout.operator(CHARACTERDESIGNER_OT_clean_animation_data.bl_idname, icon='BRUSH_DATA')
        rig = context.object
        if (context.mode == 'POSE' and rig is not None and rig.animation_data
                and rig.animation_data.action and context.selected_pose_bones):
            _button(layout, 'START', 'Preview Key Reduction', icon='KEYFRAME_HLT')
        return

    box = layout.box()
    title = box.row(align=True)
    title.label(text='Key Reduction')
    _button(title, 'UPDATE', '', icon='PREFERENCES')
    if 'before' not in state:
        box.label(text='Key counts unavailable', icon='INFO')
    elif state['removed']:
        fraction = state['removed'] / state['reduced_before']
        box.label(text=f"{state['reduced_before']:,} → {state['reduced_after']:,}  (−{fraction:.1%})")
    else:
        box.label(text='No keys removed')
    row = box.row(align=True)
    for view, label in (('ORIGINAL', 'Original'), ('REDUCED', 'Reduced')):
        _button(row, view, label, depress=state['view'] == view)
    row = box.row(align=True)
    row.operator_context = 'INVOKE_DEFAULT'
    _button(row, 'APPLY', 'Apply Reduction', icon='CHECKMARK')
    _button(row, 'DISCARD', 'Discard', icon='X')
