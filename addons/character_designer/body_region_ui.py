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
    # Append values to keep existing saved selections unchanged.
    ('FEET', 'Both Feet', 'Foot controls only; keep the leg IK and Pole'),
    ('LEFT_FOOT', 'Left Foot', 'Left foot controls only; keep the leg IK and Pole'),
    ('RIGHT_FOOT', 'Right Foot', 'Right foot controls only; keep the leg IK and Pole'),
)
_FOOT_SIDES = {'FEET': ('L', 'R'), 'LEFT_FOOT': ('L',), 'RIGHT_FOOT': ('R',)}
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


def _foot_records(rig):
    from . import foot_controls
    return foot_controls.records(rig)


def _rebuild_feet(context, rig, sides):
    from . import foot_controls_rebuild
    return foot_controls_rebuild.rebuild(context, rig, sides=sides)


def _state(rig, region):
    if region not in _FOOT_SIDES:
        return _service().state(rig, region)
    records = _foot_records(rig)
    found = [side in records for side in _FOOT_SIDES[region]]
    return {'status': 'BOUND' if all(found) else 'MIXED' if any(found) else 'MISSING',
            'label': _REGION_LABELS[region]}


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
        description='Choose the body part whose controls need rebuilding',
        items=REGION_ITEMS,
        default='LEGS',
    )


class CHARACTERDESIGNER_OT_body_region(Operator):
    bl_idname = 'character_designer.body_region'
    bl_label = 'Regional Controls'
    bl_description = 'Rebuild or release the selected part while keeping its original bones, custom shapes and the other body controls'
    bl_options = {'REGISTER', 'UNDO'}

    region: EnumProperty(name='Part', items=REGION_ITEMS, default='LEGS')
    action: EnumProperty(
        items=(
            ('RELEASE', 'Release Controls', 'Remove only the selected part\'s generated controls so its original bones can be adjusted'),
            ('BIND', 'Rebuild Controls', 'Build this released part\'s controls around the adjusted original bones'),
            ('REBUILD', 'Rebuild Controls', 'Fit this existing part\'s controls to the current pose, keeping original bones and custom shapes'),
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
                if self.region in _FOOT_SIDES:
                    raise ValueError('Choose a leg or arm part to release its original bones for editing.')
                _service().release(context, context.object, self.region)
                message = 'Controls released. Adjust the original bones, then rebuild this part.'
            elif self.action == 'BIND':
                if self.region in _FOOT_SIDES:
                    raise ValueError('Use Rebuild Controls for this existing foot part.')
                _service().bind(context, context.object, self.region)
                message = 'Controls rebuilt for the selected part.'
            elif self.action == 'REBUILD':
                if self.region in _FOOT_SIDES:
                    _rebuild_feet(context, context.object, _FOOT_SIDES[self.region])
                else:
                    _service().rebuild(context, context.object, self.region)
                message = 'Controls rebuilt for the selected part; custom shapes kept.'
            else:
                raise ValueError('Choose Release to Edit or Rebuild Controls.')
            self.report({'INFO'}, message)
            return {'FINISHED'}
        except _ERRORS as exc:
            if getattr(exc, '_character_designer_body_rollback_restored', False) is True:
                action = {'RELEASE': 'Release', 'BIND': 'Control generation',
                          'REBUILD': 'Rebuild'}.get(self.action, 'Change')
                self.report({'WARNING'},
                            f'{action} was not applied; the previous settings were restored. {exc}')
                # FINISHED closes the native Undo boundary after rollback has
                # written/recreated IDs; it does not mean the request succeeded.
                return {'FINISHED'}
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


class CHARACTERDESIGNER_OT_accept_edited_leg_rest(Operator):
    bl_idname = 'character_designer.accept_edited_leg_rest'
    bl_label = 'Fit Controls to Edited Legs'
    bl_description = 'Keep the edited leg bones and fit both leg controls; earlier animations retain their original compatibility checks'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _editable_rig(context)

    def execute(self, context):
        if not self.poll(context):
            self.report({'WARNING'}, 'Make the local Main Rig active in Object or Pose Mode.')
            return {'CANCELLED'}
        from . import body_rest_adoption
        try:
            body_rest_adoption.accept_current_legs(context, context.object)
        except _ERRORS as exc:
            self.report({'WARNING'}, f'Edited leg fitting was not applied. {exc}')
            if getattr(exc, '_character_designer_body_rollback_restored', False) is True:
                return {'FINISHED'}
            return {'CANCELLED'}
        self.report({'INFO'}, 'Leg controls fitted; current bones, paint and earlier animations kept.')
        return {'FINISHED'}


def draw(layout, context):
    """Only read the small region record here; backend admission runs on click."""
    settings = _settings(context)
    rig = getattr(context, 'object', None)
    if settings is None or rig is None or getattr(rig, 'type', None) != 'ARMATURE':
        return
    region = settings.region
    box = layout.box()
    box.label(text='Body Part', icon='BONE_DATA')
    box.prop(settings, 'region', text='Part')
    try:
        result = _state(rig, region)
    except _ERRORS as exc:
        result = {'status': 'ERROR', 'reason': str(exc)}
    status = result.get('status', 'ERROR')
    status_text = _STATUS_LABELS.get(status, 'Needs Review')
    icon = 'CHECKMARK' if status == 'BOUND' else 'ERROR' if status == 'ERROR' else 'INFO'
    box.label(text=status_text, icon=icon)
    row = box.row()
    row.enabled = _editable_rig(context) and status in {'BOUND', 'RELEASED'}
    rebuilding = status == 'RELEASED'
    operator = row.operator(
        'character_designer.body_region',
        text='Rebuild Controls',
        icon='ARMATURE_DATA',
    )
    operator.region = region
    operator.action = 'BIND' if rebuilding else 'REBUILD'
    if status == 'ERROR':
        for line in textwrap.wrap(result.get('reason', ''), 36):
            box.label(text=line)


def draw_advanced(layout, context):
    """Optional teardown belongs in the existing Body Setup Advanced section."""
    settings = _settings(context)
    rig = getattr(context, 'object', None)
    if (settings is None or rig is None or getattr(rig, 'type', None) != 'ARMATURE'
            or settings.region in _FOOT_SIDES):
        return
    try:
        if _state(rig, settings.region).get('status') != 'BOUND':
            return
    except _ERRORS:
        return
    row = layout.row()
    row.enabled = _editable_rig(context)
    operator = row.operator('character_designer.body_region',
                            text='Release to Edit', icon='UNLINKED')
    operator.region, operator.action = settings.region, 'RELEASE'
    # Draw only a cheap saved-state hint. The click authenticates the complete
    # same-ID history and existing graph; this hint grants no editing authority.
    if (settings.region == 'LEGS'
            and rig.get('character_designer_worklist_main_session_v1') is not None
            and 'character_designer_current_leg_rest_acceptance_v1' not in rig.data):
        row = layout.row()
        row.enabled = _editable_rig(context)
        row.operator('character_designer.accept_edited_leg_rest', icon='ARMATURE_DATA')


BODY_REGION_UI_CLASSES = (
    CharacterDesignerBodyRegionState,
    CHARACTERDESIGNER_OT_body_region,
    CHARACTERDESIGNER_OT_accept_edited_leg_rest,
)
