"""Remove redundant native pose channels and retain their clip-local input defaults."""
import json
from collections import defaultdict

from . import animation_export_inputs as export
from .animation_preview_curves import _editable, _repeat_mode
from .control_pose_offset import _bag, _curve_name

from .animation_worklist_fingerprint import CLEAN_DEFAULTS as DEFAULTS


# Solved controller clips can accumulate small matrix-decomposition residuals.
# 0.005% is the maximum deviation from neutral for removal on prepared clips.
NEUTRAL_SCALE_TOLERANCE = 5.e-5
_SCALE_ROUNDING = 3.e-6
_DEFAULT_PROPS = {'location', 'scale', 'scale.x', 'scale.y', 'scale.z'}


def _stored(action):
    record = json.loads(action.get(DEFAULTS, '{}'))
    if not isinstance(record, dict) or any(
            not isinstance(groups, dict) or any(
                    not isinstance(props, list)
                    or not all(isinstance(p, str) and p in _DEFAULT_PROPS for p in props)
                for props in groups.values()) for groups in record.values()):
        raise ValueError('Invalid cleaned transform data on ' + action.name)
    return record


def missing_defaults(rig, action, handle, curves=None):
    """An authored replacement curve or driver always owns its component."""
    if DEFAULTS not in action:
        return {}
    paths = _stored(action).get(str(handle), {})
    if not paths:
        return {}
    if curves is None:
        curves = export._curves(action, handle)
    occupied = {(c.data_path, c.array_index) for c in curves}
    ad = rig.animation_data
    if ad:
        occupied.update((c.data_path, c.array_index) for c in ad.drivers)
    result = {}
    for name, props in paths.items():
        bone = rig.pose.bones.get(name)
        if bone is None:
            continue
        for entry in props:
            prop, _, axis = entry.partition('.')
            path = bone.path_from_id(prop)
            for index in ('xyz'.index(axis),) if axis else range(3):
                if (path, index) not in occupied:
                    result[path, index] = 1.0 if prop == 'scale' else 0.0
    return result


def restore_defaults(rig, action, handle):
    previous = []
    try:
        for (path, axis), default in missing_defaults(rig, action, handle).items():
            value = rig.path_resolve(path)
            if value[axis] != default:
                previous.append((value, axis, value[axis]))
                value[axis] = default
    except Exception:
        for value, axis, old in reversed(previous):
            value[axis] = old
        raise


def _flat(curve, default, *, tolerance=0., allow_repeat=False):
    if not _editable(curve) or not curve.is_valid or not curve.keyframe_points:
        return False
    if (allow_repeat and len(curve.keyframe_points) == 1 and curve.extrapolation == 'CONSTANT'
            and all(mod.type == 'CYCLES' for mod in curve.modifiers)):
        # A single key has no segment, tangent, or nonzero cycle offset.
        point = curve.keyframe_points[0]
        return point.type in {'KEYFRAME', 'GENERATED'} and abs(point.co.y - default) <= tolerance
    if curve.modifiers and (not allow_repeat or _repeat_mode(curve) != 'REPEAT'):
        return False
    previous_bezier = False
    for point in curve.keyframe_points:
        if (not abs(point.co.y - default) <= tolerance or point.interpolation not in {'LINEAR', 'CONSTANT', 'BEZIER'}
                or point.type not in {'KEYFRAME', 'GENERATED'}):
            return False
        # The incoming handle belongs to the segment starting at the previous key.
        # Check both sides, including mixed interpolation and extrapolated ends.
        is_bezier = point.interpolation == 'BEZIER'
        if ((previous_bezier or (is_bezier and curve.extrapolation == 'LINEAR'))
                and not abs(point.handle_left.y - default) <= tolerance
                or is_bezier and not abs(point.handle_right.y - default) <= tolerance):
            return False
        previous_bezier = is_bezier
    return True


def _scale_tolerance(curve):
    # A linear extrapolation with even tiny slope can exceed the key envelope.
    return _SCALE_ROUNDING if curve.extrapolation == 'CONSTANT' else 0.


def hide_unused_scale(rig, action, handle, *, bone_names=None):
    """Hide constant, unused scale axes in the Graph Editor. Preserve curve evaluation."""
    names = set(bone_names) if bone_names is not None else None
    ad = rig.animation_data
    driven = {(curve.data_path, curve.array_index) for curve in ad.drivers} if ad else set()
    hidden = 0
    for curve in export._curves(action, handle):
        if curve.hide or not curve.data_path.endswith('.scale'):
            continue
        info = _curve_name(curve)
        if not info:
            continue
        name, _, axis = info
        bone = rig.pose.bones.get(name)
        if (bone is None or not 0 <= axis < 3 or (names is not None and name not in names)
                or (curve.data_path, axis) in driven or not curve.keyframe_points):
            continue
        tolerance = _scale_tolerance(curve)
        if (_flat(curve, 1., tolerance=tolerance) or (bone.lock_scale[axis]
                and _flat(curve, curve.keyframe_points[0].co.y, tolerance=tolerance))):
            curve.hide = True
            hidden += 1
    return hidden


def clean(rig, action, handle, *, published=False, scales_only=False):
    """Remove neutral Scale axes and connected Location groups. Keep clip time bounds."""
    curves = export._curves(action, handle)
    bag = _bag(action, handle)
    ad = rig.animation_data
    active = ad is not None and ad.action == action and ad.action_slot_handle == handle
    driven = {(c.data_path, c.array_index) for c in ad.drivers} if ad else set()
    driven_paths = {path for path, _ in driven}
    properties = {'scale'} if scales_only else {'scale', 'location'}
    groups = defaultdict(list)
    for curve in curves:
        info = _curve_name(curve)
        if info and info[1] in properties:
            groups[info[:2]].append(curve)
    candidates = {}
    for (name, prop), group in groups.items():
        pb = rig.pose.bones.get(name)
        if pb is None or (prop == 'location' and not pb.bone.use_connect):
            continue
        if prop == 'scale':
            removable = []
            for curve in group:
                axis = curve.array_index
                # Linear extrapolation can grow even a tiny initial slope.
                tolerance = NEUTRAL_SCALE_TOLERANCE if curve.extrapolation == 'CONSTANT' else 0.
                if (0 <= axis < 3 and (curve.data_path, axis) not in driven
                        and (not active or abs(pb.scale[axis] - 1.) <= tolerance)
                        and _flat(curve, 1., tolerance=tolerance, allow_repeat=True)):
                    removable.append(curve)
            if removable:
                candidates[name, prop] = removable
        elif (sorted(c.array_index for c in group) == [0, 1, 2]
                and group[0].data_path not in driven_paths
                and all(v == 0. for v in pb.location) and all(_flat(c, 0.) for c in group)):
            candidates[name, prop] = group
    if not candidates:
        return dict(curves=0, keys=0)

    # Preserve slot endpoints and at least one native keyed channel, including
    # a constant-only take. A full group is kept if it is the sole time anchor.
    removed = {c.as_pointer() for group in candidates.values() for c in group}
    spans = [(c, tuple(c.range())) for c in curves if c.keyframe_points or c.sampled_points]
    for boundary, bound in enumerate((min, max)):
        edge = bound(span[boundary] for c, span in spans)
        touching = [c for c, span in spans if span[boundary] == edge]
        if all(c.as_pointer() in removed for c in touching):
            info = _curve_name(touching[0])
            group = candidates.pop(info[:2])
            removed.difference_update(c.as_pointer() for c in group)
    if not candidates:
        return dict(curves=0, keys=0)

    record = _stored(action)
    slot_defaults = record.setdefault(str(handle), {})
    total = 0
    for (name, prop), group in candidates.items():
        props = slot_defaults.setdefault(name, [])
        if prop not in props:
            if prop == 'scale' and len(group) != 3:
                for curve in group:
                    axis_default = 'scale.' + 'xyz'[curve.array_index]
                    if axis_default not in props:
                        props.append(axis_default)
            else:
                props.append(prop)
        total += sum(len(c.keyframe_points) for c in group)
    encoded = json.dumps(record, sort_keys=True, separators=(',', ':'))
    if published:
        scales = [(rig.pose.bones[name], c.array_index) for (name, prop), group in candidates.items()
                  if active and prop == 'scale' for c in group]
        _clean_published(action, bag, candidates, encoded, scales)
    else:
        # A failed worker discards its entire unpublished Action.
        action[DEFAULTS] = encoded
        for group in candidates.values():
            for curve in group:
                bag.fcurves.remove(curve)
        action.update_tag()
    return dict(curves=len(removed), keys=total)


def _clean_published(action, bag, candidates, encoded, scales):
    """Retain a native copy of each affected curve until the in-place edit commits."""
    import bpy
    old = action.get(DEFAULTS)
    backup = bpy.data.actions.new('.Animation Cleanup Recovery')
    copies = []
    previous = [(bone, axis, bone.scale[axis]) for bone, axis in scales if bone.scale[axis] != 1.]
    try:
        slot = backup.slots.new('OBJECT', 'Recovery')
        store = backup.layers.new('Recovery').strips.new(type='KEYFRAME').channelbag(slot, ensure=True)
        for curves in candidates.values():
            for curve in curves:
                copies.append((store.fcurves.new_from_fcurve(curve), curve.group))
        action[DEFAULTS] = encoded
        for curves in candidates.values():
            for curve in curves:
                bag.fcurves.remove(curve)
        for bone, axis, _ in previous:
            bone.scale[axis] = 1.
        action.update_tag()
    except Exception:
        if old is not None:
            action[DEFAULTS] = old
        elif DEFAULTS in action:
            del action[DEFAULTS]
        for saved, group in copies:
            if bag.fcurves.find(saved.data_path, index=saved.array_index) is None:
                restored = bag.fcurves.new_from_fcurve(saved)
                restored.group = group
        for bone, axis, value in previous:
            bone.scale[axis] = value
        action.update_tag()
        raise
    finally:
        if backup.users == 0:
            bpy.data.actions.remove(backup, do_unlink=False)


def prepared_binding(context):
    rig = context.object
    if rig is None or rig.type != 'ARMATURE' or rig.mode != 'POSE':
        return None
    ad = rig.animation_data
    if not ad or not ad.action:
        return None
    from .animation_preview_session import SESSION
    if SESSION in rig:
        return None
    for row in getattr(context.scene, 'character_designer_controller_motions', ()):
        if row.state == 'READY' and row.target == rig and (ad.action, ad.action_slot_handle) in (
                (row.controller_action, row.controller_slot), (row.alternate_action, row.alternate_slot)):
            return rig, ad.action, ad.action_slot_handle
    return None
