"""Explicit Align and Relax actions with independent bone transactions.

Align uses Basic Setup to validate the finger interior. Relax works directly
on selected Edit Mode chains, preserving endpoints, curvature and bone rolls.
"""
import json
import math
import bpy

from mathutils import Vector

from . import finger_bank as bank, finger_definition as definition
from . import finger_internal as internal, finger_targets as targets
from . import finger_symmetry as symmetry

EPS = 1e-6
STRAIGHT = ('INDEX', 'MIDDLE', 'RING', 'PINKY')
RELAX_STATE = 'character_designer_finger_relax_state'


def _bone_collection(rig):
    from .finger_bones import _bone_collection as read
    return read(rig)


def _head(bone):
    from .finger_bones import _head as read
    return read(bone)


def _tail(bone):
    from .finger_bones import _tail as read
    return read(bone)


def _record(obj, key):
    slot = obj.character_designer_finger_bank.slots.get(key)
    if not slot or not slot.guide.record or targets.reference_error(obj, key):
        raise ValueError(f'{key}: capture/recheck Finger Basic Setup first.')
    if not slot.guide.confirmed or not slot.guide.use_basis:
        raise ValueError(f'{key}: confirm a Basis Reference before changing bone positions.')
    return json.loads(slot.guide.record)


def _rest(chain):
    return [dict(item, connected=bone.use_connect) for item, bone in zip(targets.signature(chain), chain)]


def _same_rest(a, b):
    return len(a) == len(b) and all(
        all(x[k] == y[k] for k in ('name', 'parent', 'deform', 'connected')) and
        all((Vector(x[k])-Vector(y[k])).length <= EPS for k in ('head', 'tail'))
        for x, y in zip(a, b))


def _nodes(chain):
    return [_head(chain[0])] + [_head(b) for b in chain[1:]] + [_tail(chain[-1])]


def _sample(path, fraction):
    points = list(map(Vector, path))
    lengths = [(b-a).length for a, b in zip(points, points[1:])]
    length = sum(lengths)
    if length < EPS or not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError('The finger path or joint position is invalid.')
    remaining = fraction*length
    for a, b, step in zip(points, points[1:], lengths):
        if step > EPS and remaining <= step+EPS:
            return a.lerp(b, min(1., remaining/step)), (b-a)/step
        remaining -= step
    return points[-1].copy(), (points[-1]-points[-2]).normalized()


def _certify_chain(obj, rig, key, nodes, bm):
    record = _record(obj, key)
    if definition._topology(bm) != record['topology']:
        record = bank.remap_record(record, bm)
    body = record.get('body')
    if not body:
        raise ValueError(f'{key}: recapture this older finger definition before bone movement.')
    volume = internal.Volume(bm, body)
    transform = obj.matrix_world.inverted() @ rig.matrix_world
    points = [transform @ Vector(p) for p in nodes]
    scale = body['length']
    margin = max(scale*1e-6, min(body['radius']*.02, scale*.001))
    for p in points[1:-1]:
        if not volume.inside(p) or volume.distance(p) < margin:
            raise ValueError(f'{key}: a planned joint leaves the captured finger interior.')
    for i, (a, b) in enumerate(zip(points, points[1:])):
        length = (b-a).length
        if length < max(EPS, scale*1e-5):
            raise ValueError(f'{key}: bone movement would collapse a segment.')
        # Original fixed ends may lie exactly on the captured cap. Permit only
        # a microscopic boundary start/end, never an external root extension.
        start, end = a.copy(), b.copy()
        for which, endpoint in ((0, a), (1, b)):
            boundary = i == 0 and which == 0 or i == len(points)-2 and which == 1
            distance = volume.distance(endpoint)
            if boundary and distance is not None and distance <= margin*1.1:
                fraction = min(.01, margin*2/length)
                inset = endpoint.lerp(b if which == 0 else a, fraction)
                if not volume.inside(inset):
                    raise ValueError(f'{key}: the fixed endpoint points outside the finger volume.')
                if which == 0: start = inset
                else: end = inset
        if volume.certify((start, end), margin*.5) is None:
            raise ValueError(f'{key}: a planned bone segment leaves the captured finger volume.')


def _snapshot_edit(rig):
    return {b.name: dict(head=b.head.copy(), tail=b.tail.copy(), roll=b.roll,
                        parent=b.parent.name if b.parent else None, connected=b.use_connect,
                        deform=b.use_deform) for b in rig.data.edit_bones}


def snapshot(context, plan):
    obj, rig = plan['obj'], plan['rig']
    with targets.edit_rig(context, rig): bones = _snapshot_edit(rig)
    return dict(bones=bones, slots={key: obj.character_designer_finger_bank.slots[key].bones for key in plan['keys']})


def _restore_edit(rig, saved):
    bones = rig.data.edit_bones
    if set(saved) != set(bones.keys()):
        raise ValueError('Bone identities changed during the position transaction.')
    mirror = rig.data.use_mirror_x
    try:
        rig.data.use_mirror_x = False
        for b in bones: b.use_connect = False
        for name, item in saved.items():
            b = bones[name]
            b.parent = bones.get(item['parent']) if item['parent'] else None
            b.head, b.tail, b.roll, b.use_deform = item['head'], item['tail'], item['roll'], item['deform']
        for name, item in saved.items(): bones[name].use_connect = item['connected']
    finally:
        rig.data.use_mirror_x = mirror


def restore(context, plan, backup):
    obj, rig = plan['obj'], plan['rig']
    with targets.edit_rig(context, rig): _restore_edit(rig, backup['bones'])
    for key, value in backup['slots'].items(): obj.character_designer_finger_bank.slots[key].bones = value


def apply(context, plan, *, after_write=None):
    """Rest positions only; return changed-segment count, roll and identity intact."""
    rig = plan['rig']
    with targets.edit_rig(context, rig):
        before = _snapshot_edit(rig)
        wanted = {}
        for chain in plan['chains']:
            if len(chain['nodes']) != len(chain['names'])+1:
                raise ValueError('The planned chain has inconsistent endpoint counts.')
            current = [rig.data.edit_bones[name] for name in chain['names']]
            if chain.get('expected') and not _same_rest(chain['expected'], _rest(current)):
                raise ValueError('The rest chain changed after planning; build a new plan before writing.')
            if ((_head(current[0])-Vector(chain['nodes'][0])).length > EPS or
                    (_tail(current[-1])-Vector(chain['nodes'][-1])).length > EPS):
                raise ValueError('Bone position plans must preserve the chain root and tip.')
            for name, head, tail in zip(chain['names'], chain['nodes'], chain['nodes'][1:]):
                if name in wanted: raise ValueError('A rest bone appears in multiple chain plans.')
                wanted[name] = Vector(head), Vector(tail)
        symmetry.guard_pose(rig, list(wanted))
        changed = {name for name, (head, tail) in wanted.items() if
                   (before[name]['head']-head).length > EPS or (before[name]['tail']-tail).length > EPS}
        if not changed: return 0
        # Connected non-finger children cannot keep their rest head if their
        # parent's tail moves. Reject, rather than silently dragging them along.
        for name, item in before.items():
            if name not in wanted and item['connected'] and item['parent'] in wanted:
                if (item['head']-wanted[item['parent']][1]).length > EPS:
                    raise ValueError(f'{name}: an unrelated connected child would move.')
        mirror = rig.data.use_mirror_x
        try:
            rig.data.use_mirror_x = False
            for b in rig.data.edit_bones: b.use_connect = False
            for name, (head, tail) in wanted.items():
                b = rig.data.edit_bones[name]
                b.head, b.tail = head, tail
                b.roll = before[name]['roll']
            for name, item in before.items(): rig.data.edit_bones[name].use_connect = item['connected']
            if after_write: after_write()
            after = _snapshot_edit(rig)
            for name, item in before.items():
                actual = after[name]
                if any(actual[k] != item[k] for k in ('parent', 'connected', 'deform')) or abs(actual['roll']-item['roll']) > EPS:
                    raise ValueError('Bone rest identity or roll changed during position synchronization.')
                expected = wanted.get(name, (item['head'], item['tail']))
                if any((actual[k]-p).length > EPS for k, p in zip(('head', 'tail'), expected)):
                    raise ValueError('Bone position verification failed; no rest changes were kept.')
            rig.update_tag(refresh={'DATA'})
        except Exception:
            _restore_edit(rig, before)
            raise
        finally:
            rig.data.use_mirror_x = mirror
    return len(changed)


def _turns(nodes):
    return [(b-a).cross(c-b) for a, b, c in zip(nodes, nodes[1:], nodes[2:])]


def _relaxed(nodes, locks=()):
    """Resample the existing curved polyline, independently between fixed nodes."""
    result = [p.copy() for p in nodes]
    anchors = sorted({0, len(nodes)-1, *locks})
    for a, b in zip(anchors, anchors[1:]):
        for i in range(a+1, b): result[i] = _sample(nodes[a:b+1], (i-a)/(b-a))[0]
    old, new = _turns(nodes), _turns(result)
    scale = sum((b-a).length for a, b in zip(nodes, nodes[1:]))
    tolerance = scale*scale*1e-7
    for before, after in zip(old, new):
        if before.length > tolerance and (after.length <= tolerance or before.dot(after) <= 0):
            if len(nodes) == 3: return [p.copy() for p in nodes]
            raise ValueError('Relax would remove or reverse the chain bend; current curvature was kept.')
    return result


def _commit_plan(plan):
    obj, rig = plan['obj'], plan['rig']
    bones = _bone_collection(rig)
    for chain in plan['chains']:
        obj.character_designer_finger_bank.slots[chain['key']].bones = json.dumps(
            {'rig': rig.name, 'chain': targets.signature([bones[n] for n in chain['names']])})


def _run(context, plan):
    before = snapshot(context, plan)
    try:
        changed = apply(context, plan)
        _commit_plan(plan)
        return dict(changed=changed, chains=len(plan['chains']), keys=plan['keys'])
    except Exception:
        restore(context, plan, before)
        raise


def _relax_state(obj):
    try:
        value = json.loads(obj.get(RELAX_STATE, '{}'))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _explicit_plan(context, digits, *, hints=()):
    obj, rig = targets.owner(context)
    symmetry.guard_rig(rig)
    side = bank.display_side(obj.character_designer_finger_bank)
    plan = dict(obj=obj, rig=rig, chains=[], keys=[])
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        candidates = targets.index(rig)
        for digit in digits:
            key = digit+'.'+side
            chains = {key: targets.resolve(obj, rig, key, candidates, hints)}
            for key, chain in chains.items():
                slot = obj.character_designer_finger_bank.slots[key]
                definition.frame(bank.scoped(context, slot.guide), require_basis=True, require_confirmed=True)
                nodes = _nodes(chain)
                root, tip = nodes[0], nodes[-1]
                axis = tip-root
                if axis.length < EPS: raise ValueError('The finger endpoints are collapsed.')
                fractions = [(p-root).dot(axis)/axis.length_squared for p in nodes]
                if any(z-a <= EPS for a, z in zip(fractions, fractions[1:])):
                    raise ValueError('The chain folds back; straight alignment would cross joints.')
                proposed = [root+axis*t for t in fractions]
                _certify_chain(obj, rig, key, proposed, bm)
                plan['chains'].append(dict(key=key, names=[b.name for b in chain], nodes=proposed, expected=_rest(chain)))
                plan['keys'].append(key)
    finally:
        bm.free()
    return plan


def align(context, digit=None, *, selected=False):
    """Align straight-finger chains on the active side; keep root/tip and roll."""
    obj, rig = targets.owner(context)
    side = bank.display_side(obj.character_designer_finger_bank)
    hints = []
    if selected:
        if context.object != rig or context.mode not in {'EDIT_ARMATURE', 'POSE'}:
            raise ValueError('Select existing straight-finger bones on Main Rig first.')
        hints = [b.name for b in _bone_collection(rig) if
                 (b.select if rig.mode == 'EDIT' else rig.pose.bones[b.name].select)]
        digits = [d for d in STRAIGHT if any(b.name in hints for k, chain in targets.index(rig).items()
                                          if k == d+'.'+side for b in chain)]
        if not digits: raise ValueError('No straight-finger chain is selected; Thumb uses Relax instead.')
    else:
        digit = digit or obj.character_designer_finger_bank.active.split('.')[0]
        digits = list(STRAIGHT) if digit == 'ALL' else [digit]
        if any(d not in STRAIGHT for d in digits):
            raise ValueError('Straight alignment excludes Thumb; use Thumb Relax to retain its curve.')
    return _run(context, _explicit_plan(context, digits, hints=hints))


def relax(context):
    """Relax selected chains; X Mirror derives existing mates from one source."""
    plan = _selected_relax_plan(context)
    rig = plan['rig']
    before = _snapshot_edit(rig)
    previous = (RELAX_STATE in rig, rig.get(RELAX_STATE))
    saved = _relax_state(rig)
    bindings = []
    try:
        changed = apply(context, plan)
        collection = _bone_collection(rig)
        for item in plan['chains']:
            key = _chain_key(item['names'])
            saved[key] = dict(rig=rig.name, armature=rig.data.name, names=item['names'],
                              result=_rest([collection[n] for n in item['names']]))
        rig[RELAX_STATE] = json.dumps(saved)
        _refresh_selected_bindings(rig, before, plan, bindings)
        return dict(changed=changed, chains=len(plan['chains']),
                    keys=[item['key'] for item in plan['chains']],
                    skipped_mirrors=plan['skipped_mirrors'])
    except Exception:
        _restore_edit(rig, before)
        for slot, value in bindings: slot.bones = value
        if previous[0]: rig[RELAX_STATE] = previous[1]
        elif RELAX_STATE in rig: del rig[RELAX_STATE]
        raise


def _chain_key(names):
    return json.dumps(list(names), separators=(',', ':'))


def _validate_selected_chain(chain):
    if any(b.hide or getattr(b, 'lock', False) for b in chain):
        raise ValueError('A selected or mirrored bone is hidden or locked.')
    scale = sum((_tail(b)-_head(b)).length for b in chain)
    tolerance = max(EPS, scale*1e-6)
    if any((_tail(b)-_head(b)).length <= tolerance for b in chain):
        raise ValueError('Relax cannot use a collapsed bone segment.')
    if any(b.parent != a or (_tail(a)-_head(b)).length > tolerance for a, b in zip(chain, chain[1:])):
        raise ValueError('Select continuous parented bone chains with touching joints; a chain has a gap.')


def _selected_relax_plan(context):
    rig = context.object
    if not rig or rig.type != 'ARMATURE' or context.mode != 'EDIT_ARMATURE':
        raise ValueError('Select bone chains in Armature Edit Mode for Relax Bones.')
    if len(context.objects_in_mode_unique_data) != 1:
        raise ValueError('Relax bone chains on one armature at a time.')
    symmetry.guard_rig(rig)
    collection = rig.data.edit_bones
    selected = {b.name for b in collection if b.select}
    if not selected: raise ValueError('Select at least one bone chain for Relax Bones.')
    chains = []
    for name in sorted(selected):
        bone = collection[name]
        if bone.parent and bone.parent.name in selected: continue
        chain = []
        while bone:
            chain.append(bone)
            children = [b for b in bone.children if b.name in selected]
            if len(children) > 1:
                raise ValueError('The selected bones branch; select separate unbranched chains.')
            bone = children[0] if children else None
        _validate_selected_chain(chain)
        chains.append(chain)
    # A selection on both sides is one pair when X Mirror is enabled. Prefer
    # the active bone's chain, otherwise L, then stable names, as the source.
    from .finger_bones import _side_key
    active = collection.active.name if collection.active and collection.active.name in selected else None
    chains.sort(key=lambda chain: (not any(b.name == active for b in chain),
                                   {'L': 0, 'R': 1}.get(_side_key(chain[0].name), 2), chain[0].name))
    plan = dict(rig=rig, chains=[], skipped_mirrors=[])
    handled = set()
    saved = _relax_state(rig)
    for chain in chains:
        names = [b.name for b in chain]
        if set(names) <= handled: continue
        if handled & set(names):
            raise ValueError('The selected ranges overlap their mirrored chains; select matching chain ranges.')
        nodes = _nodes(chain)
        prior = saved.get(_chain_key(names), {})
        if not isinstance(prior, dict): prior = {}
        reused = (prior.get('armature') == rig.data.name and prior.get('names') == names and
                  _same_rest(prior.get('result', []), _rest(chain)))
        proposed = nodes if reused else _relaxed(nodes)
        plan['chains'].append(dict(key=names[0], names=names, nodes=proposed, expected=_rest(chain)))
        handled.update(names)
        if not rig.data.use_mirror_x: continue
        other_names = [bpy.utils.flip_name(name) for name in names]
        if other_names == names: continue  # Unsided / central chain.
        if any(a == b or collection.get(b) is None for a, b in zip(names, other_names)):
            plan['skipped_mirrors'].append(names[0])
            continue
        if handled & set(other_names):
            raise ValueError('The selected ranges overlap their mirrored chains; select matching chain ranges.')
        for other_selected in chains:
            overlap = set(other_names) & {b.name for b in other_selected}
            if overlap and {b.name for b in other_selected} != set(other_names):
                raise ValueError('Select matching chain ranges on both sides, or select only one side with X Mirror.')
        other = [collection[name] for name in other_names]
        _validate_selected_chain(other)
        if any(bpy.utils.flip_name(name) != source for source, name in zip(names, other_names)):
            raise ValueError('The mirrored bone names are ambiguous.')
        tolerance = max(EPS, sum((_tail(b)-_head(b)).length for b in chain)*1e-6)
        if any((symmetry.reflect(a)-b).length > tolerance for a, b in
               ((nodes[0], _head(other[0])), (nodes[-1], _tail(other[-1])))):
            raise ValueError('Mirrored chains need symmetric root and tip positions; align their endpoints or disable X Mirror.')
        mirrored = [symmetry.reflect(p) for p in proposed]
        mirrored[0], mirrored[-1] = _head(other[0]), _tail(other[-1])
        plan['chains'].append(dict(key=other_names[0], names=other_names, nodes=mirrored, expected=_rest(other)))
        handled.update(other_names)
    return plan


def _refresh_selected_bindings(rig, before, plan, backups):
    """Keep already-valid captured bone identities current, without mesh scans."""
    names = {name for item in plan['chains'] for name in item['names']}
    collection = _bone_collection(rig)
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.library or obj.override_library or not obj.is_editable: continue
        for slot in obj.character_designer_finger_bank.slots:
            if not slot.bones or slot.is_property_readonly('bones'): continue
            try:
                saved = json.loads(slot.bones)
                if not isinstance(saved, dict): continue
                old = saved.get('chain', [])
                if not isinstance(old, list) or not all(isinstance(b, dict) for b in old): continue
                if saved.get('rig') != rig.name or not any(b.get('name') in names for b in old): continue
                if not all(b.get('name') in before and b.get('parent', '') == (before[b['name']]['parent'] or '') and
                           b.get('deform') == before[b['name']]['deform'] and
                           all((Vector(b[k])-before[b['name']][k]).length <= EPS for k in ('head', 'tail')) for b in old): continue
                saved['chain'] = targets.signature([collection[b['name']] for b in old])
                previous = slot.bones
                slot.bones = json.dumps(saved)
                backups.append((slot, previous))
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
                # Optional reference metadata cannot gate a bone-only action.
                continue
