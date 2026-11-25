"""Retain both shin inputs on an independent IK Action for subsequent FK/IK keys."""
from bisect import bisect_right
import json
import math

from mathutils import Euler, Quaternion, Vector

_RECORD = 'character_designer_knee_rotation_inputs_v1'


def _rotation(bone):
    return {'QUATERNION':'rotation_quaternion', 'AXIS_ANGLE':'rotation_axis_angle'}.get(
        bone.rotation_mode, 'rotation_euler')


def _reference(curves, fallback, frames):
    return [[t, *[float(c.evaluate(t)) if c else float(v) for c,v in zip(curves,fallback)]] for t in frames]


def _points(action, slot, path, fallback):
    from .animation_export_inputs import _curves
    indexed = {c.array_index:c for c in _curves(action,slot) if c.data_path == path and not c.mute
               and not (c.group and c.group.mute)}
    curves = [indexed.get(i) for i in range(len(fallback))]
    times = {float(p.co.x) for c in curves if c for p in c.keyframe_points}
    start, end = map(float, action.frame_range)
    times.update((start,end))
    # Keep native authored times. Sample between them for curved input and place
    # a left limit at a step so a later FK edit cannot turn a hold into a ramp.
    ordered = sorted(times)
    curved = any(c and (c.modifiers or any(p.interpolation != 'LINEAR' for p in c.keyframe_points))
                 for c in curves)
    if curved:
        for a,b in zip(ordered,ordered[1:]):
            count = min(65536,max(1,math.ceil((b-a)*4)))
            times.update(a + (b-a)*i/count for i in range(1,count))
            if b-a > .001:
                times.add(b-.0001)
    return _reference(curves,fallback,sorted(times))


def capture(action, slot, rig, shin_names):
    captured = {}
    for name in shin_names:
        bone = rig.pose.bones[name]
        prop = _rotation(bone)
        path = bone.path_from_id(prop)
        captured[path] = dict(mode=bone.rotation_mode, fk=_points(action,slot,path,getattr(bone,prop)))
    return captured


def store(action, slot, source):
    if source:
        packet = {path:dict(mode=data['mode'], fk=data['fk'],
                            ik=_points(action,slot,path,data['fk'][0][1:])) for path,data in source.items()}
        action[_RECORD] = json.dumps(packet, allow_nan=False, separators=(',',':'))


def _at(series, times, frame):
    i = bisect_right(times,frame)-1
    if i < 0:
        return series[0][1:]
    if i+1 == len(series) or frame == times[i]:
        return series[i][1:]
    fraction = (frame-times[i])/(times[i+1]-times[i])
    return [a+(b-a)*fraction for a,b in zip(series[i][1:],series[i+1][1:])]


def _quat(mode, components):
    if mode == 'QUATERNION':
        return Quaternion(components).normalized()
    if mode == 'AXIS_ANGLE':
        axis = Vector(components[1:])
        return Quaternion(axis,components[0]) if axis.length_squared else Quaternion()
    return Euler(components,mode).to_quaternion()


def range_values(action, bag, limb, mode, frame, mode_curve):
    """Translate subsequent shin keys in the newly switched interval into its active basis."""
    saved = action.get(_RECORD)
    if not saved:
        return {}
    bone = limb['chain'][1]
    from bpy.utils import escape_identifier
    prefix = 'pose.bones["'+escape_identifier(bone)+'"].'
    packet = json.loads(saved)
    matches = [(path,data) for path,data in packet.items() if path.startswith(prefix)]
    if not matches:
        return {}
    path, data = matches[0]
    before, after = (data['ik'],data['fk']) if mode=='FK' else (data['fk'],data['ik'])
    left_times, right_times = [r[0] for r in before],[r[0] for r in after]
    stop = next((float(p.co.x) for p in mode_curve.keyframe_points if p.co.x > frame+1.e-5), None)
    last = min(stop, max(left_times[-1],right_times[-1])) if stop is not None else max(left_times[-1],right_times[-1])
    if last-frame <= .0101:
        return {}
    channels = {c.array_index:c for c in bag.fcurves if c.data_path == path}
    if len(channels)!=len(before[0])-1:
        raise ValueError('The stored shin rotation channels are incomplete.')
    times = {float(p.co.x) for c in channels.values() for p in c.keyframe_points if frame < p.co.x < last}
    # Represent a sparse original FK channel when the inactive IK input was constant.
    # Generated subframe keys obey Blender's edit spacing. Preserve all existing times.
    extras = sorted(set(right_times) | ({last-.012} if stop == last else {last}))
    native_times = sorted({frame,last,*times})
    from bisect import bisect_left, insort
    for t in extras:
        if t <= frame+.0101 or (stop is not None and t >= stop-.0101) or t > last:
            continue
        i = bisect_left(native_times,t)
        if all(abs(t-x)>.0101 for x in native_times[max(0,i-1):i+1]):
            times.add(t)
            insort(native_times,t)
    if stop is None:
        times.add(last)
    output = { (path,i):[] for i in channels }
    for t in sorted(times):
        old = _at(before,left_times,t)
        new = _at(after,right_times,t)
        current = [channels[i].evaluate(t) for i in range(len(channels))]
        # Preserve the artist's rotation relative to the previous solver seed.
        q = _quat(data['mode'],current) @ _quat(data['mode'],old).inverted() @ _quat(data['mode'],new)
        if data['mode']=='QUATERNION':
            q.make_compatible(Quaternion(new))
            components = q
        elif data['mode']=='AXIS_ANGLE':
            axis, angle = q.to_axis_angle()
            components = (angle,*axis)
        else:
            components = q.to_euler(data['mode'],Euler(new,data['mode']))
        for i in channels:
            output[path,i].append((t,float(components[i])))
    if stop is not None and stop == last:
        for points in output.values():
            if points:
                time,value = points[-1]
                points[-1] = (time,value,'CONSTANT')
    return output
