"""Release and rebuild one limb region without replacing the whole body rig."""
import textwrap
import bpy

from bpy.props import EnumProperty
from bpy.types import Operator, PropertyGroup


REGION_ITEMS = (
    ('LEGS', 'Both Legs', 'Both legs, including their foot controls'),
    ('LEFT_LEG', 'Left Leg', 'Left leg, including its foot controls'),
    ('RIGHT_LEG', 'Right Leg', 'Right leg, including its foot controls'),
    ('ARMS', 'Both Arms', 'Both arms, including their wrist controls'),
    ('LEFT_ARM', 'Left Arm', 'Left arm, including its wrist controls'),
    ('RIGHT_ARM', 'Right Arm', 'Right arm, including its wrist controls'),
)
_REGION_LABELS = {item[0]: item[1] for item in REGION_ITEMS}
_ERRORS = (ValueError, RuntimeError, KeyError, TypeError, ReferenceError)
_STATUS_LABELS = {
    'BOUND': 'Controls Ready',
    'RELEASED': 'Released',
    'MISSING': 'Not Built',
    'MIXED': 'Different States',
    'ERROR': 'Needs Review',
}


def _service():
    from . import body_region
    return body_region


def _settings(context):
    return getattr(getattr(context, 'scene', None), 'character_designer_body_region', None)


def _ui_single_binding(rig, data):
    if getattr(data, 'users', 0) == 1:
        return True
    # Recovery ID references also count as users. Keep draw-time admission
    # cheap; the service authenticates every reference before changing data.
    if not hasattr(rig, 'get'):
        return False
    known = (rig.get('character_designer_worklist_main_restored_v1') is not None
             or rig.get('character_designer_worklist_main_session_v1') is not None
             or any(str(key).startswith('character_designer_body_region_') for key in data.keys()))
    if not known:
        return False
    bindings = [obj for obj in bpy.data.objects if obj.type == 'ARMATURE' and obj.data is data]
    return len(bindings) == 1 and bindings[0] is rig


def _editable_rig(context):
    rig = getattr(context, 'object', None)
    data = getattr(rig, 'data', None)
    return bool(
        rig is not None and getattr(rig, 'type', None) == 'ARMATURE'
        and getattr(context, 'mode', None) in {'OBJECT', 'POSE'}
        and getattr(rig, 'is_editable', False)
        and not getattr(rig, 'library', None)
        and not getattr(rig, 'override_library', None)
        and data is not None and not getattr(data, 'library', None)
        and getattr(data, 'is_editable', True)
        and _ui_single_binding(rig, data)
    )


class CharacterDesignerBodyRegionState(PropertyGroup):
    region: EnumProperty(
        name='Part',
        description='Choose the controls to release and rebuild; original bones are kept',
        items=REGION_ITEMS,
        default='LEGS',
    )


class CHARACTERDESIGNER_OT_body_region(Operator):
    bl_idname = 'character_designer.body_region'
    bl_label = 'Regional Controls'
    bl_description = 'Release or rebuild controls for this part while keeping its original bones and the other body controls'
    bl_options = {'REGISTER', 'UNDO'}

    region: EnumProperty(name='Part', items=REGION_ITEMS, default='LEGS')
    action: EnumProperty(
        items=(
            ('RELEASE', 'Release Controls', 'Remove only the selected part\'s generated controls so its original bones can be adjusted'),
            ('BIND', 'Rebuild Controls', 'Build this released part\'s controls around the adjusted original bones'),
        ),
        default='RELEASE',
    )

    @classmethod
    def poll(cls, context):
        return _editable_rig(context)

    def execute(self, context):
        if not self.poll(context):
            self.report({'WARNING'}, 'Select a local armature in Object or Pose Mode with its own armature data.')
            return {'CANCELLED'}
        try:
            if self.action == 'RELEASE':
                _service().release(context, context.object, self.region)
                message = 'Controls released. Adjust the original bones, then rebuild this part.'
            elif self.action == 'BIND':
                _service().bind(context, context.object, self.region)
                message = 'Controls rebuilt for the selected part.'
            else:
                raise ValueError('Choose Release Controls or Rebuild Controls.')
            self.report({'INFO'}, message)
            return {'FINISHED'}
        except _ERRORS as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw(layout, context):
    """Only read the small region record here; backend admission runs on click."""
    settings = _settings(context)
    rig = getattr(context, 'object', None)
    if settings is None or rig is None or getattr(rig, 'type', None) != 'ARMATURE':
        return
    region = settings.region
    box = layout.box()
    box.label(text='Edit One Part', icon='BONE_DATA')
    box.prop(settings, 'region', text='Part')
    try:
        result = _service().state(rig, region)
    except _ERRORS as exc:
        result = {'status': 'ERROR', 'reason': str(exc)}
    status = result.get('status', 'ERROR')
    label = result.get('label') or _REGION_LABELS.get(region, 'Selected Part')
    status_text = _STATUS_LABELS.get(status, 'Needs Review')
    icon = 'CHECKMARK' if status == 'BOUND' else 'ERROR' if status == 'ERROR' else 'INFO'
    box.label(text=f'{label}: {status_text}', icon=icon)
    row = box.row()
    row.enabled = _editable_rig(context) and status in {'BOUND', 'RELEASED'}
    rebuilding = status == 'RELEASED'
    operator = row.operator(
        'character_designer.body_region',
        text='Rebuild Controls' if rebuilding else 'Release Controls',
        icon='ARMATURE_DATA' if rebuilding else 'UNLINKED',
    )
    operator.region = region
    operator.action = 'BIND' if rebuilding else 'RELEASE'
    if status == 'RELEASED':
        box.label(text='Adjust original bones, then rebuild.')
        if getattr(context, 'mode', None) not in {'OBJECT', 'POSE'}:
            box.label(text='Finish bone editing to rebuild.', icon='INFO')
    elif status == 'BOUND':
        box.label(text='Release first to edit original bones.')
    elif status == 'MIXED':
        box.label(text='Select the left or right side separately.')
    elif status == 'MISSING':
        box.label(text='No controls to release for this part.')
    reason = result.get('reason', '')
    for line in textwrap.wrap(reason, 36):
        box.label(text=line)
    box.label(text='Original bones and weights are kept.')


BODY_REGION_UI_CLASSES = (
    CharacterDesignerBodyRegionState,
    CHARACTERDESIGNER_OT_body_region,
)
