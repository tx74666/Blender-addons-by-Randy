"""Native in-place wrist keys with complete operation-local C recovery copies.

Each component gets a fresh native curve copy immediately before its first key
write. Curve edits made since preparation are current author data. Success
removes only the owned unbound recovery Action; failure reads full columns and
restores the original curve in place without animation evaluation flushing.
No whole-Action/Source proof, global inventory, publication or disk access.
"""
from array import array
import math
import struct

MAX_CURVE_POINTS = 20001
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
_COORDINATES = {'co', 'co_ui', 'handle_left', 'handle_right'}


def _require(value, message):
    if not value:
        raise ValueError(message)


def _same(a, b):
    return a is b or a is not None and b is not None and a.as_pointer() == b.as_pointer()


def _settings(value):
    result = {}
    for prop in value.bl_rna.properties:
        if prop.is_readonly or prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
            continue
        item = getattr(value, prop.identifier)
        result[prop.identifier] = tuple(item) if getattr(prop, 'is_array', False) else item
    return result


def _assign(value, settings):
    for name, item in settings.items():
        setattr(value, name, item)


def _equal(a, b):
    if type(a) is not type(b):
        return False
    if type(a) is float:
        return struct.pack('=d', a) == struct.pack('=d', b)
    if type(a) is dict:
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if type(a) in (tuple, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    return a == b


def _columns(points, budget):
    count = len(points)
    _require(count <= MAX_CURVE_POINTS, 'This wrist curve exceeds the local keying limit.')
    if not count:
        return ()
    result = []
    for prop in points[0].bl_rna.properties:
        if prop.is_readonly:
            continue
        name, kind = prop.identifier, prop.type
        _require(kind in {'FLOAT', 'INT', 'BOOLEAN', 'ENUM'}, 'Unsupported local wrist key field: ' + name)
        is_array = bool(getattr(prop, 'is_array', False))
        width = int(prop.array_length) if is_array else 1
        _require(width > 0 and sum(bool(v) for v in getattr(prop, 'array_dimensions', ())) <= 1,
                 'Unsupported wrist key array: ' + name)
        code = {'FLOAT': 'd', 'INT': 'i', 'BOOLEAN': 'b', 'ENUM': 'i'}[kind]
        itemsize = array(code).itemsize
        _require(itemsize == {'d': 8, 'i': 4, 'b': 1}[code], 'Unsupported key buffer width.')
        size = count * width * itemsize
        _require(budget[0] + size <= MAX_SNAPSHOT_BYTES, 'The local wrist snapshot exceeds its buffer limit.')
        budget[0] += size
        values = array(code, [0]) * (count * width)
        try:
            points.foreach_get(name, values)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            raise ValueError('Cannot read the complete wrist key field: ' + name) from error
        identifiers = None
        if kind == 'ENUM':
            _require(not is_array and not getattr(prop, 'is_enum_flag', False), 'Unsupported key enum flags.')
            items = [(item.value, item.identifier) for item in prop.enum_items if item.identifier]
            identifiers = dict(items)
            _require(items and len(identifiers) == len(items) and all(v in identifiers for v in values),
                     'Unknown native wrist key enum: ' + name)
        elif kind == 'FLOAT':
            _require(all(map(math.isfinite, values)), 'The wrist curve contains non-finite keys.')
        elif kind == 'BOOLEAN':
            _require(all(v in (0, 1) for v in values), 'Invalid wrist key selection value.')
        result.append((name, kind, is_array, width, values, identifiers))
    _require(_COORDINATES <= {row[0] for row in result}, 'The complete wrist coordinate schema is unavailable.')
    return tuple(result)


def _curve(curve, budget):
    _require(not curve.modifiers and not curve.sampled_points and not curve.lock
             and not (curve.group and curve.group.lock) and not getattr(curve, 'is_driver', False),
             'Unlock this wrist curve and remove its modifiers/samples before Auto Key.')
    return dict(curve=curve, pointer=int(curve.as_pointer()), group=curve.group,
        settings=_settings(curve), count=len(curve.keyframe_points), columns=_columns(curve.keyframe_points, budget))


def _write(points, column):
    # No scalar fallback: individual RNA setters flush all keyed pose inputs.
    name, _kind, _is_array, _width, values, _identifiers = column
    try:
        points.foreach_set(name, values)
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        raise ValueError('The complete wrist backup needs native bulk recovery: ' + name) from error


def _restore_curve(saved):
    """Same-count in-place recovery; never clear/add/update or replay policies."""
    curve = saved['curve']
    _require(int(curve.as_pointer()) == saved['pointer'] and _same(curve.group, saved['group']),
             'The original wrist FCurve or group identity changed.')
    points = curve.keyframe_points
    _require(len(points) == saved['count'], 'Restore the native key count before restoring wrist fields.')
    current = _settings(curve)
    _require(current.keys() == saved['settings'].keys(), 'The wrist curve metadata schema changed.')
    changed = [name for name in current if not _equal(current[name], saved['settings'][name])]
    _require(set(changed) <= {'active_keyframe_index'}, 'Another writer changed this wrist curve policy.')
    columns = {row[0]: row for row in saved['columns']}
    if saved['count']:
        _write(points, columns['co'])
        _write(points, columns['co_ui'])
        for column in saved['columns']:
            if column[0] not in _COORDINATES:
                _write(points, column)
        for name in changed:
            setattr(curve, name, saved['settings'][name])
        _write(points, columns['handle_left'])
        _write(points, columns['handle_right'])


def _matches(saved, curve):
    if not _same(saved['curve'], curve) or not _same(curve.group, saved['group']):
        return False
    if len(curve.keyframe_points) != saved['count'] or not _equal(_settings(curve), saved['settings']):
        return False
    current = _columns(curve.keyframe_points, [0])
    return len(current) == len(saved['columns']) and all(a[:4] == b[:4]
        and a[4].tobytes() == b[4].tobytes() and a[5] == b[5] for a, b in zip(current, saved['columns']))


def _binding(plan):
    rig, action, owner = plan['rig'], plan['action'], plan.get('variant')
    ad = rig.animation_data
    _require(ad and _same(ad.action, action) and ad.action_slot and ad.action_slot.handle == plan['slot']
        and _same(plan['target'].id_data, rig), 'The current Custom Action, slot or wrist owner changed.')
    _require(type(owner) is dict and type(owner.get('expected_action_users')) is int
        and action.users == owner['expected_action_users'], 'This Custom Action gained an unowned native user.')
    bag = owner.get('bag')
    _require(bag is not None and bag.slot_handle == plan['slot'] and _same(bag.id_data, action),
             'The current wrist channelbag owner changed.')
    return bag


def _policy(context):
    edit, settings = context.preferences.edit, context.scene.tool_settings
    flags = tuple(bool(getattr(owner, name, False)) for owner, name in (
        (edit, 'use_keyframe_insert_available'), (edit, 'use_auto_keyframe_insert_needed'),
        (edit, 'use_visual_keying'), (settings, 'use_keyframe_cycle_aware'),
        (settings, 'use_keyframe_insert_keyingset')))
    _require(not any(flags[2:]), 'Visual, Cycle Aware and active Keying Set options need their own keying workflow.')
    return (settings.auto_keying_mode, getattr(settings, 'keyframe_type', 'KEYFRAME'),
        getattr(edit, 'keyframe_new_interpolation_type', None), getattr(edit, 'keyframe_new_handle_type', None), flags)


def _curves(domain):
    return tuple(domain['bag'].fcurves.find(domain['path'], index=i) for i in range(domain['size']))


def _has_frame(curve, frame):
    count = len(curve.keyframe_points)
    lo, hi = 0, count
    while lo < hi:
        mid = (lo + hi) // 2
        if float(curve.keyframe_points[mid].co[0]) < frame:
            lo = mid + 1
        else:
            hi = mid
    return any(0 <= i < count and abs(float(curve.keyframe_points[i].co[0]) - frame) < 1e-5 for i in (lo - 1, lo))


def validate_keying(context, plan):
    domain = plan.get('key_domain')
    if domain is None:
        return
    _binding(plan)
    _require(not domain['closed'] and _policy(context) == domain['policy']
        and context.scene.frame_current + context.scene.frame_subframe == plan['frame']
        and bool(context.scene.tool_settings.use_keyframe_insert_auto) == plan['autokey'],
        'The frame or Auto Key policy changed.')
    curves = _curves(domain)
    for curve in curves:
        if curve is not None:
            _lease_curve(curve)
    if domain['policy'][4][0]:
        _require(all(curve is not None for curve in curves), 'Only Insert Available needs existing wrist channels.')
    if domain['policy'][0] == 'REPLACE_KEYS':
        _require(all(curve is not None and _has_frame(curve, plan['frame']) for curve in curves),
                 'Replace Keys needs a wrist key on this frame.')


def prepare_keying(context, plan):
    """Metadata only; the actual first component write gets a fresh native C copy."""
    plan['key_domain'] = None
    if not plan['autokey']:
        return
    _require(plan['action'] is not None and plan.get('variant') is not None, 'Auto Key needs the editable Custom Action.')
    bag = _binding(plan)
    _require(context.scene.keying_sets.active is None, 'The active Keying Set needs its own keying workflow.')
    policy = _policy(context)
    _require(policy[0] in {'ADD_REPLACE_KEYS', 'REPLACE_KEYS'}, 'Unsupported Auto Key mode.')
    path = plan['target'].path_from_id(plan['rotation_path'])
    size = plan['rotation_size']
    _require(plan['rotation_path'] in {'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle'}
        and size == len(getattr(plan['target'], plan['rotation_path'])) and size in (3, 4), 'Unsupported wrist representation.')
    plan['key_domain'] = dict(bag=bag, path=path, size=size, saved=[None] * size, policy=policy,
        owned_new={}, owned_groups={}, attempted=set(), values_before=tuple(getattr(plan['target'], plan['rotation_path'])),
        snapshot_points=0, closed=False, started=False, store=None)
    validate_keying(context, plan)


def _restore_domain(plan):
    domain = plan['key_domain']
    if domain['closed']:
        return
    _binding(plan)
    if domain['store'] is not None and domain['store']['action'] is not None:
        _check_backup(domain['store'])
    for i in sorted(domain['attempted'], reverse=True):
        saved = domain['saved'][i]
        current = domain['bag'].fcurves.find(domain['path'], index=i)
        if saved is None:
            if current is None:
                continue
            _require(domain['owned_new'].get(i) == int(current.as_pointer())
                and len(current.keyframe_points) == 1, 'An unowned wrist channel needs recovery review.')
            _require(_delete_key(plan, i) and domain['bag'].fcurves.find(domain['path'], index=i) is None,
                     'The native inserted wrist channel did not remove exactly.')
        else:
            _require(current is not None and _same(current, saved['curve']) and _same(current.group, saved['group']),
                     'The original wrist FCurve or group was replaced.')
            count = len(current.keyframe_points)
            _require(count in {saved['count'], saved['count'] + 1}, 'Another writer changed the wrist key count.')
            if count != saved['count']:
                _require(_delete_key(plan, i), 'The native inserted wrist key did not remove.')
                current = domain['bag'].fcurves.find(domain['path'], index=i)
                _require(current is not None and _same(current, saved['curve'])
                    and len(current.keyframe_points) == saved['count'], 'The original wrist FCurve identity or count changed.')
            source = _curve(saved['backup'], [0])
            restore = dict(source, curve=saved['curve'], pointer=saved['pointer'],
                           group=saved['group'], settings=saved['settings'])
            saved['restore_snapshot'] = restore
            _restore_curve(restore)
    for record in domain['owned_groups'].values():
        _remove_group(plan, record)


def _close(plan):
    domain = plan['key_domain']
    if domain['closed']:
        return
    store = domain['store']
    if store is None:
        _clear_domain(plan)
        return
    _require(store['shared'].get('_wrist_key_backup') is store, 'The local backup owner changed.')
    store['finished'].add(id(domain))
    if store['finished'] != set(store['plans']):
        return
    if store['action'] is not None:
        _check_backup(store)
        _remove_backup(store['action'])
    # Clear all participants only after native cleanup succeeded. A last-side
    # cleanup failure must not discard the first side's rollback material.
    for participant in tuple(store['plans'].values()):
        _clear_domain(participant)
    shared = store['shared']
    _require(shared.get('_wrist_key_backup') is store, 'The local backup owner changed.')
    del shared['_wrist_key_backup']
    store['plans'].clear()


def _verify_domain(plan):
    domain = plan['key_domain']
    if domain['closed']:
        return
    _binding(plan)
    for i in domain['attempted']:
        saved = domain['saved'][i]
        current = domain['bag'].fcurves.find(domain['path'], index=i)
        if saved is None:
            _require(current is None, 'The new wrist channel was not removed exactly.')
        else:
            snapshot = saved.get('restore_snapshot')
            _require(snapshot is not None and current is not None and _matches(snapshot, current),
                     'The original wrist backup fields did not restore exactly.')
    _close(plan)


def _rollback(plan):
    _restore_domain(plan)
    _verify_domain(plan)


def key_rotation(context, plan):
    """Native same-slot insertion; fresh per-curve C backup immediately before writing."""
    domain = plan.get('key_domain')
    if domain is None:
        return False
    validate_keying(context, plan)
    _require(not domain['started'], 'This local wrist key operation already started.')
    domain['started'] = True
    _backup_store(plan)
    plan['key_rollback'] = lambda: _rollback(plan)
    plan['key_restore'] = lambda: _restore_domain(plan)
    plan['key_verify'] = lambda: _verify_domain(plan)
    plan['key_commit'] = lambda: _close(plan)
    options = {'INSERTKEY_REPLACE'} if domain['policy'][0] == 'REPLACE_KEYS' else set()
    if domain['policy'][4][0]: options.add('INSERTKEY_AVAILABLE')
    if domain['policy'][4][1]: options.add('INSERTKEY_NEEDED')
    needed = domain['policy'][4][1]
    values = tuple(getattr(plan['target'], plan['rotation_path']))
    for i, value in enumerate(values):
        _require(math.isfinite(float(value)), 'The current wrist rotation is non-finite.')
        _binding(plan)
        _require(_policy(context) == domain['policy']
            and context.scene.frame_current + context.scene.frame_subframe == plan['frame']
            and bool(context.scene.tool_settings.use_keyframe_insert_auto) == plan['autokey'],
            'The frame or Auto Key policy changed during keying.')
        current = domain['bag'].fcurves.find(domain['path'], index=i)
        if current is not None:
            domain['saved'][i] = _snapshot_curve(plan, current)
        elif domain['policy'][4][0] or domain['policy'][0] == 'REPLACE_KEYS':
            _require(False, 'The requested existing wrist key channel disappeared.')
        saved = domain['saved'][i]
        before_group = domain['bag'].groups.get(plan['target'].name) if saved is None else None
        _require(saved is not None or before_group is None or not before_group.lock, 'Unlock this wrist key group before Auto Key.')
        domain['attempted'].add(i)
        try:
            inserted = plan['target'].keyframe_insert(data_path=plan['rotation_path'], index=i,
                frame=plan['frame'], group=plan['target'].name, options=options, keytype=domain['policy'][1])
        finally:
            current = domain['bag'].fcurves.find(domain['path'], index=i)
            if saved is None and current is not None:
                domain['owned_new'][i] = int(current.as_pointer())
            if saved is None:
                _record_group(plan, before_group)
        _require(saved is None or current is not None and _same(current, saved['curve']) and _same(current.group, saved['group']),
                 'Native keying replaced the wrist FCurve or group identity.')
        _require(saved is not None or current is None or current.group is None
            or current.group.name == plan['target'].name, 'Native keying created an unexpected group.')
        if current is None:
            _require(needed and saved is None and abs(float(value) - domain['values_before'][i]) < 2e-6,
                     'A changed wrist rotation component was not keyed.')
        else:
            _require((inserted or needed) and abs(float(current.evaluate(plan['frame'])) - float(value)) < 2e-6
                and (needed or _has_frame(current, plan['frame'])), 'The current wrist rotation key did not verify.')
    return True


def _lease_curve(curve):
    _require(not curve.modifiers and not curve.sampled_points and not curve.lock
        and not (curve.group and curve.group.lock) and not getattr(curve, 'is_driver', False),
        'Unlock this wrist curve and remove its modifiers/samples before Auto Key.')
    count = len(curve.keyframe_points)
    _require(0 < count <= MAX_CURVE_POINTS,
        'An existing empty or oversized wrist curve needs its own editing workflow.')
    names = set()
    for prop in curve.keyframe_points[0].bl_rna.properties:
        if prop.is_readonly:
            continue
        _require(prop.type in {'FLOAT', 'INT', 'BOOLEAN', 'ENUM'}, 'Unsupported local wrist key schema.')
        _require(not getattr(prop, 'is_enum_flag', False), 'Unsupported local wrist key enum flags.')
        names.add(prop.identifier)
    _require(_COORDINATES <= names, 'The complete wrist coordinate schema is unavailable.')
    return count


def _backup_store(plan):
    domain = plan['key_domain']
    if domain['store'] is not None:
        return domain['store']
    shared = plan.get('transaction')
    _require(type(shared) is dict, 'A fresh local wrist transaction is required.')
    store = shared.get('_wrist_key_backup')
    if store is None:
        store = dict(action=None, bag=None, pointer=None, plans={}, finished=set(), shared=shared)
        shared['_wrist_key_backup'] = store
    _require(store['shared'] is shared, 'The local key backup transaction changed.')
    store['plans'][id(domain)] = plan
    domain['store'] = store
    return store


def _check_backup(store):
    action, bag = store['action'], store['bag']
    _require(action is not None and int(action.as_pointer()) == store['pointer']
        and not action.library and not action.use_fake_user and action.users == 0
        and bag is not None and _same(bag.id_data, action),
        'The owned unbound wrist backup gained a foreign user or changed identity; keep it for recovery.')


def _new_backup(plan):
    import bpy
    store = _backup_store(plan)
    if store['action'] is not None:
        _check_backup(store)
        return store['action'], store['bag']
    action = bpy.data.actions.new('Wrist local key recovery')
    try:
        slot = action.slots.new(id_type='OBJECT', name='Wrist key recovery')
        layer = action.layers.new('Wrist key recovery')
        strip = layer.strips.new(type='KEYFRAME')
        bag = strip.channelbags.new(slot)
    except Exception:
        _remove_backup(action)
        raise
    store.update(action=action, bag=bag, pointer=int(action.as_pointer()))
    _check_backup(store)
    return action, bag


def _copy_curve(backup_bag, source_curve):
    return backup_bag.fcurves.new_from_fcurve(source_curve)


def _remove_backup(action):
    import bpy
    _require(not action.library and not action.use_fake_user and action.users == 0,
        'The wrist recovery Action gained an unknown user; it was preserved.')
    bpy.data.actions.remove(action, do_unlink=False)


def _snapshot_curve(plan, curve):
    domain = plan['key_domain']
    count = _lease_curve(curve)
    _require((domain['snapshot_points'] + count) * 128 <= MAX_SNAPSHOT_BYTES,
        'The native wrist backup exceeds its local memory limit.')
    meta = dict(curve=curve, pointer=int(curve.as_pointer()), group=curve.group,
        settings=_settings(curve), count=count)
    _action, bag = _new_backup(plan)
    meta['backup'] = _copy_curve(bag, curve)
    _check_backup(domain['store'])
    domain['snapshot_points'] += count
    _require(len(meta['backup'].keyframe_points) == count and meta['backup'].group is None,
        'The native complete wrist backup did not copy exactly.')
    return meta


def _delete_key(plan, index):
    # This native path tags NO_FLUSH, unlike RNA point remove/clear/add/update.
    return plan['target'].keyframe_delete(data_path=plan['rotation_path'], index=index, frame=plan['frame'])


def _clear_domain(plan):
    domain = plan['key_domain']
    domain['closed'] = True
    domain['saved'] = []
    domain['owned_new'].clear()
    domain['owned_groups'].clear()
    domain['attempted'].clear()
    domain['store'] = None
    for name in ('key_rollback', 'key_restore', 'key_verify', 'key_commit'):
        plan[name] = None


def _record_group(plan, before):
    domain = plan['key_domain']
    current = domain['bag'].groups.get(plan['target'].name)
    if before is not None:
        _require(current is not None and _same(current, before), 'The existing native wrist group identity changed.')
    elif current is not None:
        _require(_same(current.id_data, plan['action']), 'The new wrist group has an unknown Action owner.')
        domain['owned_groups'][int(current.as_pointer())] = dict(group=current,
            pointer=int(current.as_pointer()), name=current.name)


def _remove_group(plan, record):
    domain = plan['key_domain']
    current = domain['bag'].groups.get(record['name'])
    if current is None:
        return
    _require(int(current.as_pointer()) == record['pointer'] and _same(current.id_data, plan['action'])
        and not current.channels, 'The newly owned wrist group gained foreign content; keep it for recovery.')
    domain['bag'].groups.remove(current)
