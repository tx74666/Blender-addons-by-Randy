"""Shared Setup -> paired staged rings -> bounded two-bone weights.

One immutable transaction source per character, multiple per-finger recipes.
Updates regenerate only the managed fingers on that source, guarded against
ANY intervening user data edit. No old snapshot silently wins over new data.
"""
import json
import math
import time
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Vector

from . import finger_bank as bank, finger_definition as definition, finger_layout as layout
from . import finger_ring_slide as slide, finger_targets as targets

SOURCE_TAG = 'character_designer_finger_workflow_source'
RESULT_TAG = 'character_designer_finger_workflow_result'


def state(context):
    obj = bank.active_object(context)
    if not obj: raise ValueError('Capture Finger Basic Setup on this mesh first.')
    return obj, obj.character_designer_finger_workflow


def settings(context):
    obj, s = state(context)
    digit = obj.character_designer_finger_bank.active.split('.')[0]
    pair = s.pairs.get(digit)
    if pair is None: raise ValueError('Prepare this finger first.')
    return obj, s, pair


def check_source(obj, s):
    if s.source and layout.fingerprint(obj) != s.signature:
        raise ValueError('Mesh, Shape Keys, attributes or weights changed since preparation. Release prepared results and prepare again; new edits were not overwritten.')


def ensure_pair(s, digit):
    pair = s.pairs.get(digit)
    if pair is None:
        pair = s.pairs.add()
        pair.name = digit
    return pair


def _initial_positions(context, obj, digit, frame, count=None):
    """Keep the existing bone-derived preparation defaults, per finger pair."""
    try:
        _, rig = targets.owner(context)
        chain = targets.pair(obj, rig, digit, targets.index(rig))[obj.character_designer_finger_bank.active]
        from .finger_bones import _head
        positions = [definition.project_distance(rig.matrix_world @ _head(bone), frame['path'])/frame['length'] for bone in chain[1:]]
        if positions and (count is None or len(positions) == count):
            return [max(.01, min(.99, t)) for t in positions], 'BONES', ''
        reason = 'Bone junction count differs from the existing markers; marker count is kept.'
    except ValueError as exc:
        reason = str(exc)  # Topology preparation never requires bones.
    count = 2 if count is None else count
    return ([.34, .68] if count == 2 else [(i+1)/(count+1) for i in range(count)]), 'TOPOLOGY', reason


def _position_profile(positions, source, reason=''):
    return json.dumps(dict(schema=1, positions=list(positions), source=source, reason=reason))


def default_positions(pair, *, automatic=False):
    raw = pair.automatic_defaults if automatic or not pair.custom_defaults else pair.custom_defaults
    if not raw:
        raise ValueError('Prepare this finger again to establish its automatic defaults; current positions are kept.')
    try:
        data = json.loads(raw)
        values = data['positions']
        if data['schema'] != 1 or not isinstance(values, list) or not values:
            raise ValueError()
        if any(type(v) not in (int, float) or not math.isfinite(v) or not .01 <= v <= .99 for v in values):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError('Stored joint defaults are invalid; current positions are kept.') from None
    return values


def _check_positions(obj, s, pair, values):
    """Validate a complete positional update before changing any RNA settings."""
    check_source(obj, s)
    if not s.source or not pair.recipe:
        raise ValueError('Prepare this finger first.')
    if len(values) != len(pair.joints):
        raise ValueError('Default joint count differs from this finger; current markers are kept.')
    if any(not math.isfinite(v) or not .01 <= v <= .99 for v in values) or any(a >= z for a, z in zip(values, values[1:])):
        raise ValueError('Joint defaults must be ordered from root to tip, without overlapping centers.')
    data = parameters(pair)
    for joint, value in zip(data['joints'], values): joint['position'] = value
    config = SimpleNamespace(**{k: v for k, v in data.items() if k != 'joints'},
                             joints=[SimpleNamespace(**j) for j in data['joints']])
    for key, recipe in json.loads(pair.recipe).items():
        slot = obj.character_designer_finger_bank.slots.get(key)
        if not slot or slot.error or slot.guide.revision != recipe['definition_id']:
            raise ValueError(f'{key}: Setup changed; prepare this finger again.')
        recipe['flip_bend'] = slot.guide.flip_bend
        build_plan(obj, s.source, recipe, config)


def save_defaults(context):
    """Only marker positions, not widths, weights, geometry or other fingers."""
    obj, s, pair = settings(context)
    values = [j.position for j in pair.joints]
    _check_positions(obj, s, pair, values)
    pair.custom_defaults = _position_profile(values, 'CUSTOM')
    return values


def restore_defaults(context, *, automatic=False):
    obj, s, pair = settings(context)
    values = default_positions(pair, automatic=automatic)
    _check_positions(obj, s, pair, values)
    previous = [j.position for j in pair.joints]
    try:
        for joint, value in zip(pair.joints, values): joint.position = value
    except Exception:
        for joint, value in zip(pair.joints, previous): joint.position = value
        raise
    return values


def prepare(context):
    obj = layout._edit(context)
    obj2, s = state(context)
    if obj != obj2: raise ValueError('Edit the captured character mesh.')
    check_source(obj, s)
    b = obj.character_designer_finger_bank
    guide = definition.state(context)
    frame = definition.frame(context, require_basis=True, require_confirmed=True, purpose='TOPOLOGY')
    current = bank.current_candidate(context)
    digit = b.active.split('.')[0]
    report = json.loads(b.survey)
    keys = [digit+'.'+side for side in ('L', 'R') if digit+'.'+side in report['candidates']]
    if len(keys) == 2 and report['warnings'].get(digit): raise ValueError(report['warnings'][digit])
    if len(keys) == 1:
        other = 'R' if keys[0].endswith('.L') else 'L'
        if any(k.endswith('.'+other) for k in report['candidates']) or not targets.opposite_absent(obj, current):
            raise ValueError('The opposite hand exists but this finger is unmatched; no single-sided topology fallback.')
    source = s.source or obj.data.copy()
    fresh = not s.source
    try:
        bodies = json.loads(s.bodies) if s.bodies else report['candidates']
        records = {}
        for key in keys:
            slot = b.slots[key]
            if not slot.guide.record or slot.error: raise ValueError(slot.error or 'Opposite Setup is incomplete.')
            r = json.loads(slot.guide.record)
            if key not in bodies: raise ValueError('Release prepared results to include this newly detected finger.')
            rows = bodies[key]['rings']
            path = [obj.matrix_world @ Vector(p) for p in r['basis']['path']]
            length = sum((q-p).length for p, q in zip(path, path[1:]))
            positions = [definition.project_distance(sum((obj.matrix_world @ source.vertices[i].co for i in row), Vector())/len(row), path)/length for row in rows]
            if any(z-a < 1e-5 for a, z in zip(positions, positions[1:])): raise ValueError('The topology range folds back.')
            records[key] = {'schema': 2, 'rings': rows, 'positions': positions, 'root': r['basis']['path'][0],
                            'tip': r['basis']['path'][-1], 'path': r['basis']['path'], 'length': length, 'definition_id': slot.guide.revision,
                            'normal': r['basis'].get('normal'), 'flip_bend': slot.guide.flip_bend,
                            'protected_rows': sorted(slide.protected_rows(source, rows))}
        pair = ensure_pair(s, digit)
        positions, origin, reason = _initial_positions(context, obj, digit, frame, len(pair.joints) or None)
        if not pair.joints:
            for t in positions:
                joint = pair.joints.add()
                joint.position = t
        pair.recipe = json.dumps(records)
        labels = {}
        try:
            _, rig = targets.owner(context)
            chains = targets.pair(obj, rig, digit, targets.index(rig))
            for key, recipe in records.items():
                for i, joint in enumerate(pair.joints):
                    a, z, offset = joint_bones(obj, rig, chains[key], recipe, joint)
                    labels[key+':'+str(i)] = {'A': a, 'B': z, 'offset': offset}
        except ValueError: pass
        pair.labels = json.dumps(labels)
        if fresh:
            source.name = '.CD Finger Workflow Source'
            source[SOURCE_TAG] = True
            s.source, s.bodies = source, json.dumps(bodies)
            s.signature = layout.fingerprint(obj)
        # Reprepare updates the automatic baseline, never current/custom values.
        pair.automatic_defaults = _position_profile(positions, origin, reason)
        return pair
    except Exception:
        if fresh and source.users == 0: bpy.data.meshes.remove(source)
        raise


def capture_joint(context):
    obj, s, pair = settings(context)
    check_source(obj, s)
    bm = bmesh.from_edit_mesh(obj.data)
    definition._index(bm)
    chosen = {e for e in bm.edges if e.select and not e.hide}
    from .finger_joint import _ordered_loop
    ring = _ordered_loop(chosen)
    candidate = bank.current_candidate(context)
    if not any({v.index for v in ring} == set(row) for row in candidate['rings'][1:-1]):
        raise ValueError('Select one complete internal ring of the active Setup finger; face-band centers must be specified as an edge loop.')
    frame = definition.frame(context, require_basis=True, purpose='TOPOLOGY')
    center = sum((obj.matrix_world @ v.co for v in ring), Vector())/len(ring)
    t = definition.project_distance(center, frame['path'])/frame['length']
    if not pair.joints: pair.joints.add()
    pair.joints[min(pair.active_joint-1, len(pair.joints)-1)].position = t
    return t


def build_plan(obj, source, recipe, pair):
    """Reuse the existing rail sampler/reuse engine, with arbitrary joint count."""
    rows, ts = recipe['rings'], recipe['positions']
    ordered = sorted(enumerate(pair.joints), key=lambda p: p[1].position)
    if not ordered: raise ValueError('Prepare at least one joint marker.')
    requests, intervals = [], []
    for i, j in ordered:
        width = j.width
        outer = width*max(j.inner_spacing, j.outer_spacing)
        if j.position-outer <= ts[0]+layout.EPS or j.position+outer >= ts[-1]-layout.EPS:
            raise ValueError(f'Joint {i+1} support region crosses the protected root/tip. Reduce width or move the marker.')
        if intervals and j.position-outer <= intervals[-1][1]+.002:
            raise ValueError('Joint support/weight regions overlap; separate the markers or reduce widths.')
        intervals.append((j.position-outer, j.position+outer))
        for flank in (-1, 0, 1): requests.append((j.position+flank*width, 'JOINT_'+str(i+1) if flank == 0 else 'SUPPORT', i, flank))
    for a, z in zip(intervals, intervals[1:]):
        requests += [(a[1]+(z[0]-a[1])*i/(pair.between+1), 'BETWEEN', -1, 0) for i in range(1, pair.between+1)]
    rings = []
    for t, kind, joint_id, flank in sorted(requests):
        points, band, fraction = layout._sample(source, rows, ts, t)
        rings.append({'t': t, 'kind': kind, 'joint': joint_id, 'flank': flank, 'points': points,
                      'band': band, 'fraction': fraction, 'blocked': False})
    plan = {'obj': obj, 'rows': rows, 'positions': ts, 'rings': rings,
            'root': [source.vertices[i].co.copy() for i in rows[0]],
            'tip': [source.vertices[i].co.copy() for i in rows[-1]], 'defined': True}
    varying = any(abs(j.inner_spacing-j.outer_spacing) > 1e-6 for _, j in ordered)
    if not varying:
        # Equal spacing factors scale the width, too.
        for ring in rings:
            if ring['flank']:
                j = pair.joints[ring['joint']]
                ring['t'] = j.position+ring['flank']*j.width*j.inner_spacing
                ring['points'], ring['band'], ring['fraction'] = layout._sample(source, rows, ts, ring['t'])
        slide.assign(plan, source, recipe.get('protected_rows'))
    else:
        if not recipe.get('normal'): raise ValueError('Asymmetric spacing needs this finger\'s bend side.')
        normal = Vector(recipe['normal'])*(1 if recipe.get('flip_bend') else -1)
        axis = (Vector(recipe['tip'])-Vector(recipe['root'])).normalized()
        bend = (normal-axis*normal.dot(axis)).normalized()
        for ring in rings:
            if not ring['flank']: continue
            j = pair.joints[ring['joint']]
            center_points = layout._sample(source, rows, ts, j.position)[0]
            center = sum(center_points, Vector())/len(center_points)
            times = []
            for p in center_points:
                radial = p-center-axis*(p-center).dot(axis)
                mix = .5+.5*radial.normalized().dot(bend)
                factor = j.outer_spacing+(j.inner_spacing-j.outer_spacing)*mix
                times.append(j.position+ring['flank']*j.width*factor)
            samples = [layout._sample(source, rows, ts, t) for t in times]
            if len({s[1] for s in samples}) != 1 or any(s[2] <= 1e-5 or s[2] >= 1-1e-5 for s in samples):
                raise ValueError('Asymmetric support crosses an existing ring. Reduce width/spacing, or use equal spacing.')
            ring.update(times=times, fractions=[s[2] for s in samples], band=samples[0][1],
                        points=[sample[0][i] for i, sample in enumerate(samples)])
    return plan


def preview_plan(obj, source, recipe, pair):
    """Invalid editing parameters still have a drawable, never writable draft."""
    try:
        return build_plan(obj, source, recipe, pair)
    except ValueError as exc:
        rows, ts = recipe['rings'], recipe['positions']
        rings = []
        for i, joint in enumerate(pair.joints):
            for flank in (-1, 0, 1):
                t = joint.position + flank*joint.width
                # No extrapolation into the protected palm/tip. Out-of-range
                # targets are explicitly labelled at the nearest safe boundary.
                sampled_t = max(ts[0], min(ts[-1], t))
                points, band, fraction = layout._sample(source, rows, ts, sampled_t)
                rings.append(dict(t=t, kind='JOINT_'+str(i+1) if not flank else 'SUPPORT',
                                  joint=i, flank=flank, points=points, band=band,
                                  fraction=fraction, blocked=True, boundary=sampled_t != t))
        return dict(obj=obj, rings=rings, warning=str(exc), preview_only=True)


def plans(context, *, all_applied=False, preview=False):
    obj, s, active = settings(context)
    if not s.source: raise ValueError('Prepare this finger first.')
    result = []
    committed = json.loads(s.results or '{}')
    for pair in s.pairs:
        if pair != active and (not all_applied or not pair.applied): continue
        if not pair.recipe: continue
        for key, recipe in json.loads(pair.recipe).items():
            slot = obj.character_designer_finger_bank.slots.get(key)
            if not slot or slot.error or slot.guide.revision != recipe['definition_id']:
                raise ValueError(f'{key}: Setup changed; prepare this finger again.')
            recipe['flip_bend'] = slot.guide.flip_bend
            config = pair
            if pair != active and key in committed:
                data = committed[key]['parameters']
                config = SimpleNamespace(**{k: v for k, v in data.items() if k != 'joints'},
                                         joints=[SimpleNamespace(**j) for j in data['joints']])
            plan = (preview_plan if preview else build_plan)(obj, s.source, recipe, config)
            plan['settings'] = config
            result.append((pair, key, recipe, plan))
    if not result: raise ValueError('Prepare the current finger first.')
    return result


def joint_bones(obj, rig, chain, recipe, joint):
    from .finger_bones import _head
    path = [obj.matrix_world @ Vector(p) for p in recipe.get('path', (recipe['root'], recipe['tip']))]
    # Both the marker and bone are projected into the same topology path below.
    length = sum((z-a).length for a, z in zip(path, path[1:]))
    options = sorted((abs(definition.project_distance(rig.matrix_world @ _head(b), path)/length-joint.position), i)
                     for i, b in enumerate(chain) if i)
    if not options or options[0][0] > .12 or len(options) > 1 and options[1][0] < options[0][0]+.025:
        raise ValueError('Joint marker has no unique nearby bone junction; move it to the intended joint.')
    i = options[0][1]
    return chain[i-1].name, chain[i].name, options[0][0]*length


def weight_rows(plan):
    slots = {vi: (plan.get('moves', {}).get(i, t), column) for i, (row, t) in enumerate(zip(plan['rows'], plan['positions'])) for column, vi in enumerate(row)}
    for ring in plan['rings']:
        for column, vi in enumerate(ring['vertex_ids']): slots[vi] = ring.get('times', [ring['t']]*len(ring['vertex_ids']))[column], column
    return slots


def weight_plan(obj, mesh, entries, context, force=False):
    """Only existing deform groups contribute to the reserved budget."""
    _, rig = targets.owner(context)
    candidates = targets.index(rig)
    by_digit, names, output, labels = {}, {g.name: g.index for g in obj.vertex_groups}, {}, {}
    missing = []
    deform = {b.name for b in rig.data.bones if b.use_deform}
    for pair, key, recipe, plan in entries:
        config = plan.get('settings', pair)
        if not config.auto_weight and not force: continue
        if pair.name not in by_digit: by_digit[pair.name] = targets.pair(obj, rig, pair.name, candidates)
        chain = by_digit[pair.name][key]
        for i, joint in enumerate(config.joints):
            a, b, offset = joint_bones(obj, rig, chain, recipe, joint)
            for n in (a, b):
                if n not in names: names[n] = len(names); missing.append(n)
            if any(obj.vertex_groups.get(n) and obj.vertex_groups[n].lock_weight for n in (a, b)): raise ValueError(f'{key}: target group is locked.')
            if not 0 <= joint.tip_weight <= joint.center_weight <= joint.root_weight <= 1:
                raise ValueError('Use decreasing root-to-tip ratios between 0 and 1.')
            triplet = sorted((r for r in plan['rings'] if r['joint'] == i), key=lambda r: r['flank'])
            if len(triplet) != 3: raise ValueError('A joint requires exactly three reference rings for weights.')
            values = (joint.root_weight, joint.center_weight, joint.tip_weight)
            labels[key+':'+str(i)] = {'A': a, 'B': b, 'offset': offset}
            for vi, (t, column) in weight_rows(plan).items():
                positions = [r.get('times', [r['t']]*len(r['vertex_ids']))[column] for r in triplet]
                if t < positions[0]-1e-6 or t > positions[2]+1e-6: continue
                if vi in output: raise ValueError('Two weight regions overlap; no order-dependent overwriting is allowed.')
                k = 0 if t <= positions[1] else 1
                f = max(0., min(1., (t-positions[k])/(positions[k+1]-positions[k])))
                ratio = values[k]+(values[k+1]-values[k])*f
                reserved = sum(g.weight for g in mesh.vertices[vi].groups if obj.vertex_groups[g.group].name in deform-{a, b})
                if not math.isfinite(reserved) or reserved > 1+1e-6 or reserved < 0:
                    raise ValueError(f'Vertex {vi}: preserved deform weights leave no valid budget.')
                remaining = max(0., 1-reserved)
                output[vi] = {names[a]: remaining*ratio, names[b]: remaining*(1-ratio)}
    return output, labels, missing


def write_weights(obj, mesh, weights, missing=()):
    names = list(obj.vertex_groups.keys())+list(missing)
    staging = obj.copy()
    try:
        staging.data = mesh
        # Blender 5.2 keeps the group-name table with the mesh. A staged source
        # may predate groups created by a previous joint operation. Append in
        # current order before indexing; never reinterpret existing indices.
        existing = list(staging.vertex_groups.keys())
        if names[:len(existing)] != existing:
            raise ValueError('The source vertex-group table changed; release and prepare again.')
        for name in names[len(existing):]: staging.vertex_groups.new(name=name)
        for vi, values in weights.items():
            for group, value in values.items(): staging.vertex_groups[group].add([vi], value, 'REPLACE')
        mesh.update()
    finally: bpy.data.objects.remove(staging)


def _bank_backup(obj):
    b = obj.character_designer_finger_bank
    return ({p: getattr(b, p) for p in ('survey', 'active', 'status', 'needs_recheck')},
            {slot.name: ({p: getattr(slot.guide, p) for p in bank.FIELDS}, slot.error, slot.bones) for slot in b.slots})


def _restore_bank(obj, backup):
    b = obj.character_designer_finger_bank
    for p, v in backup[0].items(): setattr(b, p, v)
    for key, (guide, error, bones) in backup[1].items():
        slot = b.slots[key]
        for p, v in guide.items(): setattr(slot.guide, p, v)
        slot.error, slot.bones = error, bones


def apply(context, *, weights_only=False, after_commit=None):
    started = time.perf_counter()
    obj = layout._edit(context)
    owner, s, active = settings(context)
    if obj != owner: raise ValueError('Return to this Setup mesh.')
    check_source(obj, s)
    entries = plans(context, all_applied=True)
    committed = json.loads(s.results or '{}')
    if not weights_only and not active.auto_weight and any(
            e[0] == active and committed.get(e[1], {}).get('parameters', {}).get('auto_weight') for e in entries):
        raise ValueError('This pair already has generated local weights. Keep Weight After Rings enabled, or release and prepare again to preserve them as the new source; topology-only must not reset them.')
    old, backup = obj.data, _bank_backup(obj)
    old_state = (s.signature, s.results, active.applied)
    created_groups = []
    active_group = obj.vertex_groups.active_index
    if weights_only and not active.applied: raise ValueError('Generate the joint rings before updating only weights.')
    if weights_only:
        saved = json.loads(s.results)
        for pair, key, recipe, plan in entries:
            if key not in saved: raise ValueError('Generated ring mapping is missing.')
            if pair == active:
                # Position/spacing changes are topology edits, never a weight-only operation.
                if saved[key]['geometry'] != geometry_parameters(pair): raise ValueError('Ring positions changed; Generate / Update Rings first.')
            plan.update(saved[key]['plan'])
        new = old.copy()
    else: new = layout.stage(s.source, [e[3] for e in entries])
    try:
        weight_entries = [e for e in entries if not weights_only or e[0] == active]
        if weights_only or any(e[3]['settings'].auto_weight for e in weight_entries):
            weights, labels, missing = weight_plan(obj, new, weight_entries, context, force=weights_only)
            before = {v.index: {g.group: g.weight for g in v.groups} for v in new.vertices}
            write_weights(obj, new, weights, missing)
            for v in new.vertices:
                expected = dict(before[v.index]); expected.update(weights.get(v.index, {}))
                actual = {g.group: g.weight for g in v.groups}
                if any(abs(actual.get(k, 0)-expected.get(k, 0)) > 1e-6 for k in expected.keys() | actual.keys()):
                    raise ValueError('Local weight verification failed; no data was committed.')
        else: weights, labels, missing = {}, {}, []
        results = {}
        for pair, key, recipe, plan in entries:
            stored_parameters = parameters(plan['settings'])
            if weights_only and pair == active: stored_parameters['auto_weight'] = True
            results[key] = {'geometry': geometry_parameters(plan['settings']), 'parameters': stored_parameters, 'plan': {
                'rows': plan['rows'], 'positions': plan['positions'], 'moves': plan.get('moves', {}),
                'rings': [{k: v for k, v in r.items() if k not in {'points', 'original_ring'}} for r in plan['rings']]}}
        bpy.ops.object.mode_set(mode='OBJECT')
        for name in missing:
            created_groups.append(obj.vertex_groups.new(name=name).name)
        obj.data = new
        bpy.ops.object.mode_set(mode='EDIT')
        if after_commit: after_commit()
        if not weights_only:
            bank.recheck(context, own_layout=True, updated_keys={e[1] for e in entries})
            for _, key, _, _ in entries:
                if obj.character_designer_finger_bank.slots[key].error: raise ValueError(key+': '+obj.character_designer_finger_bank.slots[key].error)
        s.signature, s.results, active.applied = layout.fingerprint(obj), json.dumps(results), True
        active.labels = json.dumps(labels)
        new.name = old.name
        if SOURCE_TAG in new: del new[SOURCE_TAG]
        new[RESULT_TAG] = True
        added = len(new.vertices)-len(old.vertices)
        if old.users == 0 and old.get(RESULT_TAG): bpy.data.meshes.remove(old)
        return {'added': added, 'weighted': len(weights), 'seconds': time.perf_counter()-started}
    except Exception:
        if obj.mode == 'EDIT': bpy.ops.object.mode_set(mode='OBJECT')
        obj.data = old
        for name in reversed(created_groups): obj.vertex_groups.remove(obj.vertex_groups[name])
        bpy.ops.object.mode_set(mode='EDIT')
        _restore_bank(obj, backup)
        s.signature, s.results, active.applied = old_state
        raise
    finally:
        obj.vertex_groups.active_index = active_group
        if new.users == 0: bpy.data.meshes.remove(new)


def geometry_parameters(pair):
    return {'between': pair.between, 'joints': [[j.position, j.width, j.inner_spacing, j.outer_spacing] for j in pair.joints]}


def parameters(pair):
    return {'between': pair.between, 'auto_weight': pair.auto_weight,
            'joints': [{p: getattr(j, p) for p in ('position', 'width', 'inner_spacing', 'outer_spacing', 'root_weight', 'center_weight', 'tip_weight')} for j in pair.joints]}


def release(context):
    obj, s = state(context)
    source = s.source
    s.source = None
    s.signature = s.bodies = s.results = ''
    for pair in s.pairs: pair.recipe, pair.applied = '', False
    if source and source.users == 0 and source.get(SOURCE_TAG): bpy.data.meshes.remove(source)
