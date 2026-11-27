"""Remove redundant native pose channels and retain their clip-local input defaults."""
import json
from collections import defaultdict

from . import animation_export_inputs as export
from .animation_preview_curves import _editable
from .control_pose_offset import _bag, _curve_name

from .animation_worklist_fingerprint import CLEAN_DEFAULTS as DEFAULTS


def _stored(action):
    record = json.loads(action.get(DEFAULTS, '{}'))
    if not isinstance(record, dict) or any(
            not isinstance(groups, dict) or any(
                not isinstance(props, list) or not all(p in ('location', 'scale') for p in props)
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
        for prop in props:
            path = bone.path_from_id(prop)
            for index in range(3):
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


def _flat(curve, default):
    if not _editable(curve) or not curve.is_valid or not curve.keyframe_points:
        return False
    previous_bezier = False
    for point in curve.keyframe_points:
        if (point.co.y != default or point.interpolation not in {'LINEAR', 'CONSTANT', 'BEZIER'}
                or point.type not in {'KEYFRAME', 'GENERATED'}):
            return False
        # The incoming handle belongs to the segment starting at the previous key.
        # Check both sides, including mixed interpolation and extrapolated ends.
        is_bezier = point.interpolation == 'BEZIER'
        if ((previous_bezier or (is_bezier and curve.extrapolation == 'LINEAR'))
                and point.handle_left.y != default) or (is_bezier and point.handle_right.y != default):
            return False
        previous_bezier = is_bezier
    return True


def clean(rig, action, handle, *, published=False):
    """Remove complete neutral XYZ groups only. Keep original clip time bounds."""
    curves = export._curves(action, handle)
    bag = _bag(action, handle)
    driven = {c.data_path for c in rig.animation_data.drivers} if rig.animation_data else set()
    groups = defaultdict(list)
    for curve in curves:
        info = _curve_name(curve)
        if info and info[1] in ('scale', 'location'):
            groups[info[:2]].append(curve)
    candidates = {}
    for (name, prop), group in groups.items():
        pb = rig.pose.bones.get(name)
        if pb is None or (prop == 'location' and not pb.bone.use_connect):
            continue
        default = 1.0 if prop == 'scale' else 0.0
        if (sorted(c.array_index for c in group) == [0, 1, 2]
                and group[0].data_path not in driven and all(v == default for v in getattr(pb, prop))
                and all(_flat(c, default) for c in group)):
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
            props.append(prop)
        total += sum(len(c.keyframe_points) for c in group)
    encoded = json.dumps(record, sort_keys=True, separators=(',', ':'))
    if published:
        _clean_published(action, bag, candidates, encoded)
    else:
        # A failed worker discards its entire unpublished Action.
        action[DEFAULTS] = encoded
        for group in candidates.values():
            for curve in group:
                bag.fcurves.remove(curve)
        action.update_tag()
    return dict(curves=len(removed), keys=total)


def _clean_published(action, bag, candidates, encoded):
    """Retain a native copy of each affected curve until the in-place edit commits."""
    import bpy
    old = action.get(DEFAULTS)
    backup = bpy.data.actions.new('.Animation Cleanup Recovery')
    copies = []
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
