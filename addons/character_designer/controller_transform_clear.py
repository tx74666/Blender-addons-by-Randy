"""Clear a mixed selection of native controls and compensated IK wrists together."""

from array import array
from contextlib import contextmanager
from uuid import uuid4

import bpy


_ROTATION_FIELDS = ('rotation_euler', 'rotation_quaternion', 'rotation_axis_angle')
# Blender's native pose.rot_clear also clears these six Bendy Bone inputs.
_BEND_FIELDS = ('bbone_rollin', 'bbone_rollout', 'bbone_curveinx', 'bbone_curveinz',
                'bbone_curveoutx', 'bbone_curveoutz')


def _native_rotation_snapshot(rig, companions):
    affected = {pb.name: pb for pb in companions}
    if rig.pose.use_mirror_x:
        for pb in companions:
            other = rig.pose.bones.get(bpy.utils.flip_name(pb.name))
            if other is not None:
                affected[other.name] = other
    from . import limb_ik as limb
    owner = rig.data.get(limb.ARMATURE_ID_KEY)
    snapshot, paths = [], set()
    for pb in affected.values():
        if (pb.bone.get(limb.OWNER_KEY) == limb.OWNER_VALUE and not limb._owned(pb.bone, owner)
                or pb.bone.get(limb.ROLE_KEY) == 'HAND_IK'):
            raise ValueError(f'{pb.name}: ambiguous or overlapping wrist ownership; no rotation was reset.')
        rotation = {field: tuple(getattr(pb, field)) for field in _ROTATION_FIELDS}
        rotation.update((field, getattr(pb, field)) for field in _BEND_FIELDS)
        paths.update(pb.path_from_id(field) for field in rotation)
        snapshot.append((pb, rotation))
    if any(curve.data_path in paths for curve in getattr(rig.animation_data, 'drivers', ())):
        raise ValueError('A driver controls a selected or mirrored rotation input; no rotation was reset.')
    return snapshot


def _restore_native_rotations(context, snapshot):
    for pb, rotation in snapshot:
        for field, saved in rotation.items():
            value = getattr(pb, field)
            if (tuple(value) if field in _ROTATION_FIELDS else value) != saved:
                setattr(pb, field, saved)
    context.view_layer.update()


def _clear_native_companions(context, rig, companions):
    # The native operator iterates actual selected pose channels in every
    # posing armature. A Python context override cannot narrow its write set.
    names = {pb.name for pb in companions}
    excluded = [pb for pb in rig.pose.bones if pb.select and pb.name not in names]
    try:
        for pb in excluded:
            pb.select = False
        if {pb.name for pb in context.selected_pose_bones or ()} != names:
            raise ValueError('The selected controls changed before clearing rotation.')
        if bpy.ops.pose.rot_clear('EXEC_DEFAULT', False) != {'FINISHED'}:
            raise ValueError('Blender could not clear the selected control rotations.')
    finally:
        for pb in excluded:
            pb.select = True
    context.view_layer.update()


def _key_snapshot(context, plan, companions, *, nonrotation=False):
    """Journal either active rotation curves or every other curve on these controls."""
    from . import limb_ik_wrist_keying as keying
    side = plan['sides'][0] if 'sides' in plan else plan
    bag = keying._binding(side)
    paths = set()
    prefixes = []
    for bone, _ in companions:
        field = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                 'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        paths.add(bone.path_from_id(field))
        prefixes.extend((bone.path_from_id() + '.', bone.path_from_id() + '['))
    prefixes = tuple(prefixes)
    def owns(path):
        return path.startswith(prefixes) and path not in paths if nonrotation else path in paths
    existing = {}
    budget = [0]
    for c in bag.fcurves:
        if owns(c.data_path) and not (c.lock or c.group and c.group.lock):
            existing[c.data_path, c.array_index] = keying._point_snapshot(c, budget)
    return bag, owns, existing, {g.as_pointer() for g in bag.groups}, side['frame']


@contextmanager
def _preserve_empty_groups(context, journal, bones):
    """Keep existing empty groups when native rollback deletes their last new channel."""
    if journal is None or context.preferences.edit.use_keyframe_insert_available:
        yield
        return
    bag = journal[0]
    retained = []
    try:
        for name in {pb.name for pb in bones}:
            original = bag.groups.get(name)
            if original is None or original.channels:
                continue
            # A native channel move or removal destroys its group when it loses
            # the last channel. Let native insertion target a temporary group.
            original.name = '.Clear rotations ' + uuid4().hex
            retained.append((original, name))
            temporary = bag.groups.new(name)
            temporary.lock, temporary.mute = original.lock, original.mute
        yield
    finally:
        for original, name in retained:
            temporary = bag.groups.get(name)
            if temporary is not None:
                for channel in tuple(temporary.channels):
                    channel.group = original
                # Moving the last channel removes the temporary group itself.
                remaining = bag.groups.get(name)
                if remaining is not None:
                    bag.groups.remove(remaining)
            original.name = name


def _restore_keys(_rig, snapshot):
    from . import limb_ik_wrist_keying as keying
    bag, owns, existing, groups, frame = snapshot
    # Native F-Curve times are single precision, including subframe insertion.
    frame = array('f', [frame])[0]
    for curve in tuple(bag.fcurves):
        path, i = curve.data_path, curve.array_index
        if not owns(path) or curve.lock or curve.group and curve.group.lock:
            continue
        saved = existing.get((path, i))
        if not saved:
            points = curve.keyframe_points
            if len(points) != 1 or points[0].co[0] != frame:
                raise RuntimeError('The new companion rotation key needs recovery.')
            # Remove this exact channel from its original slot. RNA key deletion
            # treats scalars and arrays differently even when both use index 0.
            bag.fcurves.remove(curve)
            continue
        if keying._matches(saved, curve):
            continue
        if curve.as_pointer() != saved['pointer'] or curve.group != saved['group']:
            raise RuntimeError('The original companion rotation channel was replaced.')
        if len(curve.keyframe_points) == saved['count'] + 1:
            old_times = next((column[4][::2] for column in saved['columns'] if column[0] == 'co'), ())
            inserted = [point for point in curve.keyframe_points if point.co[0] == frame]
            if frame in old_times or len(inserted) != 1:
                raise RuntimeError('The inserted companion rotation key needs recovery.')
            curve.keyframe_points.remove(inserted[0], fast=True)
        keying._restore_curve(saved)
        if not keying._matches(saved, curve):
            raise RuntimeError('A companion rotation curve did not restore exactly.')
    for group in tuple(bag.groups):
        if group.as_pointer() not in groups and not group.channels:
            bag.groups.remove(group)


def reset_mixed_rotation(context, wrists, companions):
    rig = context.object
    if any(obj != rig for obj in context.objects_in_mode):
        raise ValueError('Use Pose Mode on one armature for a mixed wrist and control reset.')
    snapshot = _native_rotation_snapshot(rig, companions)
    from . import limb_ik_wrist_rotation as backend
    request = {'target': wrists[0]} if len(wrists) == 1 else {'targets': wrists}
    plan = backend.prepare_wrist_neutral(context, **request)
    keys = _key_snapshot(context, plan, snapshot) if context.scene.tool_settings.use_keyframe_insert_auto else None
    other_keys = _key_snapshot(context, plan, snapshot, nonrotation=True) if keys is not None else None
    selected = [pb for pb, _ in snapshot]
    selected.extend(side['target'] for side in plan.get('sides', (plan,)))
    with _preserve_empty_groups(context, keys, selected):
        try:
            _clear_native_companions(context, rig, companions)
            if other_keys is not None:
                _restore_keys(rig, other_keys)
            # The wrist service refreshes the native/FK reference at apply time:
            # a selected parent has now reached its own cleared rotation.
            return backend.apply_wrist_neutral(context, plan)
        except Exception:
            try:
                if keys is not None:
                    _restore_keys(rig, keys)
                    _restore_keys(rig, other_keys)
            finally:
                _restore_native_rotations(context, snapshot)
            raise
