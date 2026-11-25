"""Remember the calibrated bind for poses the artist actually keyed."""

import hashlib
import json
from base64 import urlsafe_b64encode
from bisect import bisect_left
from uuid import uuid4

from . import control_pose_assets, natural_bind_graph as graph


KEY = 'character_designer_keyed_limb_bind_v1'


def _curves(action, slot):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                if bag.slot_handle == slot:
                    yield from bag.fcurves


def _point(curve, frame):
    points = curve.keyframe_points
    lo, hi = 0, len(points)
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid].co.x < frame:
            lo = mid + 1
        else:
            hi = mid
    if lo < len(points) and points[lo].co.x == frame:
        return points[lo].co.y
    return None


def _keys(action, slot, frame, scope):
    if not action:
        return {}
    result = {}
    for f in _curves(action, slot):
        if f.data_path not in scope:
            continue
        value = _point(f, frame)
        if value is not None:
            result[f.data_path, f.array_index] = value
    return result


def _wire(data):
    return json.dumps(data, separators=(',', ':'), ensure_ascii=False)


def capture(rig, action, slot, frame, selection, rows):
    names = {n for n, *_ in rows} | set(selection)
    paths = sorted(rig.pose.bones[name].path_from_id(field) for name in names
                   for field in control_pose_assets._FIELDS)
    values = _keys(action, slot, frame, set(paths))
    return _wire({'paths': paths, 'selection': sorted(selection),
                  'before': [[*key, value] for key, value in values.items()]})


def _clip(ledger, action, slot):
    for clip in ledger['clips'].values():
        if clip.get('action') != action:
            continue
        if clip['slot'] == slot:
            return clip
        if clip['whole_clip'] and clip['slot'] == 0 and len(action.slots) == 1 and action.slots[0].handle == slot:
            clip['slot'] = slot  # An explicitly new, empty Action has acquired its first slot.
            return clip
    return None


def _fresh_clip(action, slot, frame):
    # Assigning a duplicate or existing animation is not a new keying action.
    return all(len(f.keyframe_points) == 1 and f.keyframe_points[0].co.x == frame
               for f in _curves(action, slot))


def _stamp(frame):
    return float(frame).hex()


def _source_curves(rig, action, slot, rows):
    paths = {(rig.pose.bones[n].path_from_id(f), i): (n, f, i) for n, f, i, *_ in rows}
    curves = {}
    for curve in _curves(action, slot):
        key = paths.get((curve.data_path, curve.array_index))
        if key:
            curves.setdefault(key, []).append(curve)
    return curves


def _curve_values(curves, key, frame):
    return [curve.evaluate(frame) for curve in curves.get(key, ())]


def remember(rig, scene, preview, action, slot, fresh):
    """Promote an unkeyed session only if native pose key data was added or edited."""
    if action is None:
        return False
    frame = scene.frame_current + scene.frame_subframe if fresh else sum(preview['frame'])
    evidence = json.loads(preview['key_witness'])
    before = {(path, i): v for path, i, v in evidence['before']}
    actual = _keys(action, slot, frame, set(evidence['paths']))
    changed = [key for key, value in actual.items() if before.get(key) != value]
    authored = set(changed)
    selection = (json.loads(preview['key_selection']) if 'key_selection' in preview
                 else evidence.get('selection'))
    rotation_keys = set(json.loads(preview.get('rotation_keys', '[]')))
    if rotation_keys and not changed:
        changed = [key for key in actual if key[0] in rotation_keys]
    if not changed and preview.get('explicit_key'):
        selected_paths = {rig.pose.bones[n].path_from_id(field) for n in selection or ()
                          for field in control_pose_assets._FIELDS}
        changed = [key for key in actual if selection is None or key[0] in selected_paths]
    empty = fresh and not any(len(bag.fcurves) for layer in action.layers for strip in layer.strips
                              for bag in getattr(strip, 'channelbags', ()))
    if not changed and not empty or fresh and not _fresh_clip(action, slot, frame):
        return False
    offsets = graph.snapshot_offsets(rig)
    serialized = _wire(offsets)
    # Native I only keys the selection. A bind reset can also have solved
    # local input channels on the unselected Original Bones.
    support = []
    for row in json.loads(preview['changes']):
        name, field, index, _, value = row
        key = (rig.pose.bones[name].path_from_id(field), index)
        if (preview.get('body_rotation') and rotation_keys and name in (selection or ())
                and field in {'location', 'scale'}):
            continue  # A rotation key must not promote transient public location or scale edits.
        if key in authored and (key[0] not in rotation_keys or actual[key] == value):
            continue
        if rotation_keys:
            # Auto Key recorded only rotation. Keep every other solved axis
            # whose native Action won't already supply the authored value.
            if actual.get(key) != value:
                support.append(row)
        elif selection is not None and name not in selection:
            support.append(row)
    if support:
        curves = _source_curves(rig, action, slot, support)
        for row in support:
            source = _curve_values(curves, tuple(row[:3]), frame)
            name, field, index = row[:3]
            path = rig.pose.bones[name].path_from_id(field)
            if path in rotation_keys and (path, index) in actual and len(source) == 1:
                # Blender now evaluates the inserted key. A calibration residual is
                # relative to that key, even if the incoming pose was far from zero.
                row[3] = source[0]
            row.append(source)
    inputs = {'time': frame, 'rows': support}
    payload = _wire([offsets, inputs]) if support else serialized
    signature = urlsafe_b64encode(hashlib.sha256(payload.encode()).digest()).decode().rstrip('=')
    existing = rig.get(KEY)
    ledger = existing.to_dict() if existing else {'token': preview['token'], 'scene': scene, 'clips': {}, 'offsets': {}}
    if ledger['token'] != preview['token'] or ledger['scene'] != scene:
        raise ValueError('The keyed bind belongs to another graph or scene.')
    ledger['offsets'][signature] = serialized
    if support:
        ledger.setdefault('inputs', {})[signature] = _wire(inputs)
    clip = _clip(ledger, action, slot)
    if clip is None:
        uid = uuid4().hex
        ledger['clips'][uid] = {'action': action, 'slot': slot, 'whole_clip': fresh, 'frames': {}}
        clip = ledger['clips'][uid]
    clip['frames'][_stamp(frame)] = {'offset': signature, 'anchors': _wire(changed),
                                     'path': changed[0][0] if changed else '', 'axis': changed[0][1] if changed else 0}
    if not clip['whole_clip']:
        clip['key_times'] = sorted({k.co.x for f in _curves(action, slot)
                                    if control_pose_assets._BONE_PATH.match(f.data_path)
                                    for k in f.keyframe_points})
    rig[KEY] = ledger
    return True


def _offset(clip, ad, time):
    point = clip['frames'].get(_stamp(time))
    if not point or clip['whole_clip']:
        return point['offset'] if point else None
    # Reuse one native key as a cheap witness on interpolation frames. A
    # deleted or retimed group must also stop affecting the spaces between keys.
    for layer in ad.action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                if bag.slot_handle != ad.action_slot_handle:
                    continue
                f = bag.fcurves.find(point['path'], index=point['axis'])
                if f and _point(f, time) is not None:
                    return point['offset']
    anchors = json.loads(point['anchors'])
    have = _keys(ad.action, ad.action_slot_handle, time, {path for path, i in anchors})
    for path, i in anchors:
        if (path, i) in have:
            point['path'], point['axis'] = path, i
            return point['offset']
    return None


def desired(rig, scene):
    ledger = rig[KEY]
    ad = rig.animation_data
    if scene != ledger['scene'] or not ad or not ad.action or ad.use_tweak_mode:
        return None
    clip = _clip(ledger, ad.action, ad.action_slot_handle)
    if not clip:
        return None
    t = scene.frame_current + scene.frame_subframe
    frames = clip['frames']
    if _stamp(t) in frames:
        offset = _offset(clip, ad, t)
        return (offset, offset, 0.) if offset else None
    times = sorted(float.fromhex(key) for key in frames) if clip['whole_clip'] else clip['key_times']
    index = bisect_left(times, t)
    if index < len(times) and times[index] == t:
        return None  # Original animation key, including the complete original solver.
    left_time = times[max(0, index - 1)]
    right_time = times[min(index, len(times) - 1)]
    a = _offset(clip, ad, left_time)
    b = _offset(clip, ad, right_time)
    if a == b:
        return (a, a, 0.) if a else None
    return (a, b, (t - left_time) / (right_time - left_time))


def input_values(rig, target):
    """Yield the pose input and weight of each unselected, calibrated channel."""
    if not target:
        return {}
    entries = rig[KEY].get('inputs', {})
    left, right, factor = target
    values = {}
    ad = rig.animation_data
    for signature, weight in ((left, 1.0 - factor), (right, factor)):
        if not weight or not signature or signature not in entries:
            continue
        inputs = json.loads(entries[signature])
        curves = _source_curves(rig, ad.action, ad.action_slot_handle, inputs['rows'])
        for name, field, axis, old, value, source in inputs['rows']:
            key = (name, field, axis)
            if _curve_values(curves, key, inputs['time']) != source:
                continue  # A subsequently keyed original FK channel takes precedence.
            total, original, adjusted = values.get(key, (0., 0., 0.))
            values[key] = (total + weight, original + weight * old, adjusted + weight * value)
    return values


def route(context, rig, target):
    ledger = rig[KEY]
    rec = graph.validate(rig)
    if rec['token'] != ledger['token']:
        raise ValueError('The keyed pose no longer owns its limb graph.')
    if target:
        left, right, factor = target
        a = json.loads(ledger['offsets'][left]) if left else None
        if left == right:
            graph.restore_offsets(context, rig, a, evaluate=False)
        else:
            b = json.loads(ledger['offsets'][right]) if right else None
            graph.interpolate_offsets(context, rig, a, b, factor, evaluate=False)
    graph.set_attached(context, rig, bool(target), evaluate=False)
