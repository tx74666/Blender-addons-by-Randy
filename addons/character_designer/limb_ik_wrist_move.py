"""Use native Grab and settle unanimated wrist inputs on confirmation."""

import math

import bpy

from . import limb_ik_wrist_owner_guard as guard
from . import limb_ik_wrist_rotation as wrist


SETTING = 'character_designer_align_wrist_on_move'
_PENDING = {}
_ROTATIONS = ('rotation_euler', 'rotation_quaternion', 'rotation_axis_angle')
_REFUSALS = (AttributeError, ImportError, ReferenceError, RuntimeError, TypeError, ValueError)


def _selection(context):
    from . import limb_ik
    from .limb_ik_wrist_rotation_ui import _owned_wrist

    if context.mode != 'POSE':
        return ()
    rig = context.object
    if rig is None or rig.pose.use_mirror_x or len(context.objects_in_mode) != 1:
        return ()
    selected = tuple(context.selected_pose_bones or ())
    if not selected or len(selected) > 2 or not all(_owned_wrist(bone) for bone in selected):
        return ()
    return tuple(bone for bone in selected if bone.bone.get(limb_ik.AUTO_ALIGN_KEY, False)
                 and not all(bone.lock_location) and float(bone.get('ik_fk', 1.)) == 1.)


def draw(layout, context):
    row = layout.row(align=True)
    if _selection(context):
        row.prop(context.window_manager, SETTING, text='Auto Align', icon='CON_ROTLIKE', toggle=True)
    label = 'Align Wrist' if len(context.selected_pose_bones) == 1 else 'Align Wrists'
    row.operator('character_designer.limb_ik_clear_wrist_rotation', text=label,
                 icon='LOOP_BACK').reference = 'FOREARM'


def _rotation_curves(rig, target):
    from bpy_extras.anim_utils import action_get_channelbag_for_slot

    ad = rig.animation_data
    bag = action_get_channelbag_for_slot(ad.action, ad.action_slot) if ad else None
    if bag is None:
        return ()
    paths = [target.path_from_id(prop) for prop in _ROTATIONS]
    return tuple(curve for path in paths for index in range(3 if path.endswith('rotation_euler') else 4)
                 if (curve := bag.fcurves.find(path, index=index)) is not None)


def _prepare(context, targets):
    rig = context.object
    targets = tuple(target for target in targets if not _rotation_curves(rig, target))
    if not targets or 'character_designer_key_preview_session' in rig:
        return None
    owner = guard.prepare(context, rig, allow_empty=True)
    action = owner['action']
    if action is not None and (not action.is_action_layered or len(action.layers) > 1
                              or any(len(layer.strips) != 1 for layer in action.layers)):
        return None
    if owner['autokey']:
        from .limb_ik_wrist_keying import _policy
        _policy(context)
    api = wrist._backend()[1]
    candidates = []
    for target in targets:
        prop = wrist._rotation_path(target.rotation_mode)
        offset = tuple(getattr(target, prop))
        neutral = {'rotation_euler': (0., 0., 0.),
                   'rotation_quaternion': (math.copysign(1., offset[0]), 0., 0., 0.),
                   'rotation_axis_angle': (0., *offset[1:])}[prop]
        if not all(map(math.isfinite, offset)) or max(abs(a-b) for a, b in zip(offset, neutral)) <= 1e-6:
            continue
        try:
            *_, entry = wrist._resolve(context, rig, api.limb, api.match, target, owner=owner)
        except _REFUSALS:
            continue
        if entry['auto_align'] and entry.get('auto_rotation_space') == 'PARENT_DELTA':
            candidates.append((target.name, tuple(target.location), prop, offset, neutral))
    if not candidates:
        return None
    # Blender supplies a new Python Context wrapper when the native modal
    # operator completes. Pin native objects rather than retaining the wrapper.
    owner.pop('context')
    return _view(context), owner, candidates


def _view(context):
    return context.window.as_pointer(), context.scene.as_pointer(), context.view_layer.as_pointer()


def _settle(context, view, owner, candidates):
    if _view(context) != view:
        return
    owner['context'] = context
    guard.validate(context, owner, allow_empty=True)
    rig = owner['rig']
    if owner['autokey']:
        guard._local_key_owner(rig, owner['action'], owner['slot'], owner['api'])
    moved = {bone.name for bone in _selection(context)}
    for name, location, prop, offset, neutral in candidates:
        target = rig.pose.bones[name]
        if name not in moved or tuple(target.location) == location or tuple(getattr(target, prop)) != offset:
            continue
        curves = _rotation_curves(rig, target)
        fresh = []
        for curve in curves:
            index = curve.array_index
            if (not owner['autokey'] or curve.data_path != target.path_from_id(prop)
                    or index >= len(offset) or len(curve.keyframe_points) != 1 or curve.modifiers
                    or curve.sampled_points or curve.lock or (curve.group and curve.group.lock)):
                break
            point = curve.keyframe_points[0]
            if abs(point.co.x - owner['frame']) > 1e-5 or abs(point.co.y - offset[index]) > 1e-6:
                break
            fresh.append((point, point.co.copy(), neutral[index]))
        else:
            # Only G's newly-created keys can land here. Existing curves were
            # excluded before the transform, including interpolated channels.
            try:
                for point, old, value in fresh:
                    point.co_ui = (old.x, value)
                setattr(target, prop, neutral)
            except _REFUSALS:
                for point, old, _ in fresh:
                    point.co_ui = old
                setattr(target, prop, offset)
                raise


class CHARACTERDESIGNER_OT_finish_wrist_move(bpy.types.Operator):
    bl_idname = 'character_designer.finish_wrist_move'
    bl_label = 'Align Unanimated Wrist'
    bl_options = {'INTERNAL'}

    def execute(self, context):
        pending = _PENDING.pop(context.window.as_pointer(), None)
        if pending is not None:
            try:
                _settle(context, *pending)
            except _REFUSALS as exc:
                self.report({'WARNING'}, str(exc))
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_native_wrist_move(bpy.types.Macro):
    bl_idname = 'character_designer.native_wrist_move'
    bl_label = 'Move and Align Wrist'
    bl_options = {'UNDO', 'INTERNAL'}


class CHARACTERDESIGNER_OT_grab_wrist(bpy.types.Operator):
    bl_idname = 'character_designer.grab_wrist'
    bl_label = 'Move IK Wrist'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return bool(context.area and context.area.type == 'VIEW_3D' and context.region
                    and context.region.type == 'WINDOW' and getattr(context.window_manager, SETTING, False)
                    and _selection(context))

    def invoke(self, context, event):
        window = context.window.as_pointer()
        _PENDING.pop(window, None)
        try:
            pending = _prepare(context, _selection(context))
        except _REFUSALS:
            pending = None
        if pending is None:
            return {'PASS_THROUGH'}
        _PENDING[window] = pending
        result = bpy.ops.character_designer.native_wrist_move('INVOKE_DEFAULT')
        if 'RUNNING_MODAL' not in result:
            _PENDING.pop(window, None)
        return {'FINISHED'} if result & {'FINISHED', 'RUNNING_MODAL'} else {'PASS_THROUGH'}


CLASSES = (CHARACTERDESIGNER_OT_finish_wrist_move, CHARACTERDESIGNER_OT_native_wrist_move,
           CHARACTERDESIGNER_OT_grab_wrist)


def register():
    setattr(bpy.types.WindowManager, SETTING, bpy.props.BoolProperty(
        name='Align Unkeyed IK Wrists', default=True, options={'SKIP_SAVE'},
        description='Align an IK wrist when confirming G if no rotation curves control its active Action slot'))
    for step in (bpy.ops.transform.translate, bpy.ops.character_designer.finish_wrist_move):
        step.get_rna_type()
        CHARACTERDESIGNER_OT_native_wrist_move.define(step.idname())


def unregister():
    _PENDING.clear()
    if hasattr(bpy.types.WindowManager, SETTING):
        delattr(bpy.types.WindowManager, SETTING)
