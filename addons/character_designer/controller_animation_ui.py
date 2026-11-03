"""Explicit whole-clip controller authoring for a selected Worklist Custom.

Drawing reads bounded row/Action metadata only. The conversion service owns
complete admission, sampling, retained publication and rollback on execution.
"""

import math
import sys

from bpy.props import IntProperty, StringProperty
from bpy.types import Operator


RECORD = 'character_designer_controller_animation_v1'


def _backend():
    from . import controller_animation
    return controller_animation


def _move_backend():
    from . import controller_animation_move
    return controller_animation_move


def _idle():
    animation = sys.modules.get(__package__ + '.animation')
    if animation is None or not hasattr(animation, '_job') or animation._job is not None:
        return False
    for name in ('animation_export', 'unity_export'):
        module = sys.modules.get(__package__ + '.' + name)
        if module is not None and getattr(module, '_ACTIVE_JOB', None) is not None:
            return False
    scan = sys.modules.get(__package__ + '.animation_worklist_ui')
    if scan is not None and any(getattr(scan, field, None) is not None
                               for field in ('_SCAN_PENDING', '_LIST_SYNC_PENDING')):
        return False
    collection = sys.modules.get(__package__ + '.animation_worklist_collection')
    batch = getattr(collection, '_batch', None)
    return batch is None or isinstance(batch, dict) and not bool(getattr(batch.get('queue'), 'busy', True))


def _has_marker(block, key):
    try:
        return block is not None and key in block
    except (TypeError, AttributeError, ReferenceError):
        return False


def _workspace_message(rig, action, active_action):
    """Presence hints only; persistent session authentication stays in execute."""
    if (getattr(getattr(rig, 'data', None), 'pose_position', None) == 'REST'
            or any(_has_marker(rig, key) for key in ('character_designer_base_pose_test_v1',
                                                    'character_designer_natural_pose_test_v1'))
            or any(_has_marker(candidate, key) for candidate in (action, active_action)
                   for key in ('character_designer_base_pose_test_session_v1',
                               'character_designer_base_pose_test_refs_v1'))):
        return 'Return to Animation before editing controller motion.'
    if _has_marker(rig, 'character_designer_body_original_mode_v1'):
        return 'Return to Controls before editing controller motion.'
    return ''


def _selection(context):
    """Presentation admission only; never a substitute for service validation."""
    scene = getattr(context, 'scene', None)
    saved = getattr(scene, 'character_designer_animation_worklist', None)
    items = getattr(saved, 'items', ())
    index = getattr(saved, 'active_index', -1)
    if saved is None or type(index) is not int or not 0 <= index < len(items):
        return None, 'Select a Custom animation.'
    item = items[index]
    if getattr(item, 'side', None) != 'CUSTOM':
        return None, 'Select Custom to convert.'
    rig, action = getattr(item, 'rig', None), getattr(item, 'custom_action', None)
    scene_objects = getattr(scene, 'objects', None)
    view_objects = getattr(getattr(context, 'view_layer', None), 'objects', None)
    ad = getattr(rig, 'animation_data', None)
    workspace_message = _workspace_message(rig, action, getattr(ad, 'action', None))
    if workspace_message:
        return None, workspace_message
    slot = getattr(ad, 'action_slot_handle', None)
    if slot is None:
        slot = getattr(getattr(ad, 'action_slot', None), 'handle', None)
    if (rig is None or action is None or getattr(rig, 'type', None) != 'ARMATURE'
            or getattr(rig, 'library', None) is not None or getattr(action, 'library', None) is not None
            or ad is None or ad.action is not action or slot != getattr(item, 'custom_slot', None)
            or scene_objects is None or scene_objects.get(rig.name) is not rig
            or view_objects is None or view_objects.get(rig.name) is not rig):
        return None, 'Activate this Custom animation before converting.'
    return {'item': item, 'rig': rig, 'action': action, 'slot': slot}, ''


def _status(context):
    selected, message = _selection(context)
    if selected is None:
        return {'state': 'UNAVAILABLE', 'message': message, 'can_convert': False,
                'has_record': False, 'frame_start': None, 'frame_end': None}
    if not _idle() or getattr(context, 'mode', 'OBJECT') not in {'OBJECT', 'POSE'}:
        return {'state': 'UNAVAILABLE', 'message': 'Finish the current animation operation first.',
                'can_convert': False, 'has_record': False, 'frame_start': None, 'frame_end': None}
    loaded = sys.modules.get(__package__ + '.controller_animation')
    if loaded is not None and __package__ + '.animation_worklist' in sys.modules:
        try:
            value = loaded.status(context)
            if isinstance(value, dict) and value.get('state') in {'CANDIDATE', 'CONTROLLER', 'UNAVAILABLE'}:
                return value
        except (ValueError, RuntimeError, ReferenceError, AttributeError, KeyError, TypeError) as error:
            return {'state': 'UNAVAILABLE', 'message': str(error), 'can_convert': False,
                    'has_record': False, 'frame_start': None, 'frame_end': None}
        return {'state': 'UNAVAILABLE', 'message': 'Controller conversion is unavailable for this animation.',
                'can_convert': False, 'has_record': False, 'frame_start': None, 'frame_end': None}
    # A retained marker is a display hint only. Execution imports the service
    # and reauthenticates it. Do not scan curves or import a service in drawing.
    keys = getattr(selected['action'], 'keys', None)
    recorded = callable(keys) and RECORD in selected['action']
    return {'state': 'CONTROLLER' if recorded else 'CANDIDATE', 'message': '',
            'can_convert': True, 'has_record': recorded,
            'frame_start': None, 'frame_end': None}


def _matches_request(operator, selected):
    item, rig, action = selected['item'], selected['rig'], selected['action']
    return (not operator.item_id or operator.item_id == item.item_id) and (
        not operator.rig_name or operator.rig_name == rig.name) and (
        not operator.action_name or operator.action_name == action.name) and (
        operator.slot_handle < 0 or operator.slot_handle == selected['slot'])


def _move_targets(context, selected):
    """Bounded presentation hint; the move service authenticates real targets."""
    if (selected is None or getattr(context, 'mode', None) != 'POSE'
            or getattr(context, 'object', None) is not selected['rig']):
        return ()
    targets = tuple(getattr(context, 'selected_pose_bones', ()) or ())
    if not 0 < len(targets) <= 4:
        return ()
    for pb in targets:
        bone = getattr(pb, 'bone', None)
        if (getattr(pb, 'id_data', None) is not selected['rig'] or bone is None
                or not callable(getattr(bone, 'get', None))
                or bone.get('character_designer_limb_ik_role') not in {'HAND_IK','FOOT_IK'}):
            return ()
    return targets


class CHARACTERDESIGNER_OT_make_controller_animation(Operator):
    bl_idname = 'character_designer.make_controller_animation'
    bl_label = 'Make Controller Animation'
    bl_description = ('Create a new Custom version with controller motion for the whole clip; '
                      'use IK for installed arms and legs and keep the previous Custom and Source')
    bl_options = {'REGISTER', 'UNDO'}

    item_id: StringProperty(options={'HIDDEN'})
    rig_name: StringProperty(options={'HIDDEN'})
    action_name: StringProperty(options={'HIDDEN'})
    slot_handle: IntProperty(default=-1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        try:
            selected, _message = _selection(context)
            return bool(selected is not None and _idle()
                        and getattr(context, 'mode', 'OBJECT') in {'OBJECT', 'POSE'})
        except (AttributeError, ReferenceError, TypeError):
            return False

    def execute(self, context):
        try:
            selected, message = _selection(context)
            if selected is None:
                raise ValueError(message)
            if not _matches_request(self, selected):
                raise ValueError('The selected animation changed. Choose its Custom again.')
            if not _idle() or getattr(context, 'mode', 'OBJECT') not in {'OBJECT', 'POSE'}:
                raise ValueError('Finish the current animation operation first.')
            backend = _backend()
            status = backend.status(context)
            if not status.get('can_convert', False):
                raise ValueError(status.get('message') or 'Controller conversion is unavailable for this animation.')
            backend.convert(context, rig=selected['rig'], activate=True)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Controller animation created for the whole clip. The previous Custom is retained.')
        area = getattr(context, 'area', None)
        if area is not None:
            area.tag_redraw()
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_apply_controller_move_across_clip(Operator):
    bl_idname = 'character_designer.apply_controller_move_across_clip'
    bl_label = 'Apply Move Across Clip'
    bl_description = ('With Auto Key off, apply the selected IK hands or feet\'s unkeyed target-local move '
                      'throughout the clip in a new Custom. Its world direction follows the target parent; '
                      'the previous Custom and Source are retained')
    bl_options = {'REGISTER', 'UNDO'}

    item_id: StringProperty(options={'HIDDEN'})
    rig_name: StringProperty(options={'HIDDEN'})
    action_name: StringProperty(options={'HIDDEN'})
    slot_handle: IntProperty(default=-1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        try:
            selected, _message = _selection(context)
            return bool(selected is not None and _idle() and RECORD in selected['action']
                        and _move_targets(context, selected))
        except (AttributeError, ReferenceError, TypeError):
            return False

    def execute(self, context):
        try:
            selected, message = _selection(context)
            if selected is None:
                raise ValueError(message)
            if not _matches_request(self, selected):
                raise ValueError('The selected animation changed. Choose its Custom again.')
            if not _idle():
                raise ValueError('Finish the current animation operation first.')
            targets = _move_targets(context, selected)
            if not targets:
                raise ValueError('Select only IK hand or foot targets in Pose Mode.')
            _move_backend().move_across_clip(context, rig=selected['rig'], targets=targets)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Move applied across the clip in a new Custom. The previous version is retained.')
        area = getattr(context, 'area', None)
        if area is not None:
            area.tag_redraw()
        return {'FINISHED'}


def draw_controller_animation(layout, context):
    """Place directly below the Worklist's Source/Custom cells."""
    status = _status(context)
    selected, _message = _selection(context)
    controller = status.get('state') == 'CONTROLLER'
    if controller:
        layout.label(text='Controller Animation', icon='ARMATURE_DATA')
    row = layout.row()
    row.enabled = bool(status.get('can_convert'))
    operator = row.operator('character_designer.make_controller_animation',
                            text='Rebuild Controller Motion' if controller else 'Make Controller Animation',
                            icon='ARMATURE_DATA')
    if selected is not None:
        operator.item_id = selected['item'].item_id
        operator.rig_name = selected['rig'].name
        operator.action_name = selected['action'].name
        operator.slot_handle = selected['slot']
    start, end = status.get('frame_start'), status.get('frame_end')
    if (type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end) and end >= start):
        layout.label(text=f'Whole clip · frames {start:g}–{end:g}')
    elif selected is not None and status.get('can_convert'):
        layout.label(text='Whole clip')
    if status.get('state') == 'UNAVAILABLE' and status.get('message'):
        layout.label(text=status['message'], icon='INFO')
    if controller:
        move_row = layout.row()
        move_row.enabled = bool(status.get('can_convert') and _move_targets(context, selected))
        operator = move_row.operator('character_designer.apply_controller_move_across_clip',
                                     text='Apply Move Across Clip', icon='TRANSFORM_MOVE')
        if selected is not None:
            operator.item_id = selected['item'].item_id
            operator.rig_name = selected['rig'].name
            operator.action_name = selected['action'].name
            operator.slot_handle = selected['slot']
        layout.label(text='Auto Key off: move target, then apply.')


CONTROLLER_ANIMATION_CLASSES = (CHARACTERDESIGNER_OT_make_controller_animation,
                               CHARACTERDESIGNER_OT_apply_controller_move_across_clip)
