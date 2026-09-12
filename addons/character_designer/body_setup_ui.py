"""Unified Body Setup actions and advanced setup information."""
from bpy.props import EnumProperty
from bpy.types import Operator


def _service():
    from . import body_setup
    return body_setup


def advanced(context):
    from . import limb_ik
    return bool(getattr(limb_ik._settings(context), 'show_body_setup_advanced', False))


def _count(value):
    return value if isinstance(value, int) else len(value or ())


def _has_generated(rig):
    """Keep removal reachable when a damaged record fails strict validation."""
    try:
        if _service().has_generated(rig):
            return True
    except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError):
        pass
    from . import (limb_ik, limb_fk_visuals, root_control, foot_controls, torso_controls,
                   spine_ik_fk, head_neck_visuals, body_detail_visuals, eye_controls)
    modules = (limb_fk_visuals, root_control, foot_controls, torso_controls,
               spine_ik_fk, head_neck_visuals, body_detail_visuals, eye_controls)
    if any(module.RECORD_KEY in rig.data for module in modules):
        return True
    owners = {module.OWNER_VALUE for module in modules} | {limb_ik.OWNER_VALUE}
    return any(bone.get(limb_ik.OWNER_KEY) in owners for bone in rig.data.bones) or any(
        pb.custom_shape and pb.custom_shape.get(limb_ik.OWNER_KEY) in owners for pb in rig.pose.bones)


class CHARACTERDESIGNER_OT_body_setup(Operator):
    bl_idname = 'character_designer.body_setup'
    bl_label = 'Body Setup'
    bl_description = 'Set up all supported body controls, or remove them while keeping native bones, weights and calibration'
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=(('GENERATE', 'Generate Body Setup', ''),
                                ('REMOVE', 'Remove Generated Controls', 'Keep the current native bind bones, pose and calibration')),
                         default='GENERATE')

    @classmethod
    def poll(cls, context):
        rig = context.object
        return bool(rig and rig.type == 'ARMATURE' and context.mode in {'OBJECT', 'POSE'}
                    and rig.is_editable and not rig.library and not rig.override_library
                    and not rig.data.library)

    def invoke(self, context, event):
        if self.action == 'REMOVE':
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        from . import limb_ik
        try:
            if self.action == 'REMOVE':
                _service().remove(context, context.object)
                message = 'Body controls removed; native bind bones, pose and calibration kept.'
            else:
                result = _service().generate(context, context.object)
                message = (f"Body Setup: {_count(result.get('created'))} added, "
                           f"{_count(result.get('reused'))} reused, {_count(result.get('skipped'))} skipped.")
            limb_ik._set_status(limb_ik._settings(context), 'SUCCESS', message)
            self.report({'INFO'}, message)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            limb_ik._set_status(limb_ik._settings(context), 'WARNING', str(exc))
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw_actions(layout, context):
    rig = context.object
    if rig is None or rig.type != 'ARMATURE':
        layout.label(text='Select the main armature.', icon='INFO')
        return
    try:
        exists = _has_generated(rig)
        layout.operator('character_designer.body_setup',
                        text='Update Body Setup' if exists else 'Generate Body Setup',
                        icon='ARMATURE_DATA').action = 'GENERATE'
        row = layout.row()
        row.alert = True
        row.enabled = exists
        row.operator('character_designer.body_setup', text='Remove Generated Controls',
                     icon='TRASH').action = 'REMOVE'
    except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
        layout.label(text=str(exc), icon='ERROR')


def draw_plan(layout, context):
    rig = context.object
    if rig is None or rig.type != 'ARMATURE':
        return
    try:
        result = _service().plan(context, rig)
        components = result.get('components', ())
        if isinstance(components, dict):
            components = [dict(value, key=key) for key, value in components.items()]
        for component in components:
            label = component.get('label') or component['key'].replace('_', ' ').title()
            status = component['status']
            reason = component.get('reason', '')
            layout.label(text=f'{label}: {reason or status.title()}',
                         icon='ERROR' if status in {'BLOCKED', 'ERROR', 'INVALID', 'NEEDS_MAPPING'} else 'INFO')
    except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
        layout.label(text=str(exc), icon='ERROR')


BODY_SETUP_UI_CLASSES = (CHARACTERDESIGNER_OT_body_setup,)
