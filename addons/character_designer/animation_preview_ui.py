"""Compare selected controls before and after key reduction in one viewport."""
import bpy
from bpy.props import EnumProperty, FloatProperty

from . import animation_preview_session as session


OPERATIONS = (
    ('START', 'Preview Key Reduction', 'Simplify selected controls on a temporary copy'),
    ('ORIGINAL', 'Original', 'Show the original motion at the current frame'),
    ('REDUCED', 'Reduced', 'Show the reduced motion at the current frame'),
    ('UPDATE', 'Adjust Reduction', 'Recalculate from the original using new tolerances'),
    ('APPLY', 'Apply', 'Write the simplified control curves back to the original action'),
    ('DISCARD', 'Discard', 'End the comparison and discard the preview'),
)


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

    def invoke(self, context, _event):
        if self.operation == 'UPDATE':
            state = session.status(context)
            if state is None:
                return {'CANCELLED'}
            self.position_mm = state['position_mm']
            self.rotation_degrees = state['rotation_degrees']
            return context.window_manager.invoke_props_dialog(self, width=285, title='Adjust Reduction')
        return self.execute(context)

    def draw(self, _context):
        self.layout.prop(self, 'position_mm')
        self.layout.prop(self, 'rotation_degrees')

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
        rig = context.object
        if (context.mode == 'POSE' and rig is not None and rig.animation_data
                and rig.animation_data.action and context.selected_pose_bones):
            _button(layout, 'START', 'Preview Key Reduction', icon='IPO_LINEAR')
        return

    box = layout.box()
    title = box.row(align=True)
    title.label(text=f"Keys: {state['before']:,} → {state['after']:,}")
    _button(title, 'UPDATE', '', icon='PREFERENCES')
    row = box.row(align=True)
    for view, label in (('ORIGINAL', 'Original'), ('REDUCED', 'Reduced')):
        _button(row, view, label, depress=state['view'] == view)
    row = box.row(align=True)
    _button(row, 'APPLY', 'Apply', icon='CHECKMARK')
    _button(row, 'DISCARD', 'Discard', icon='X')
