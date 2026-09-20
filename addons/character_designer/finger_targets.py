"""Character-scoped, read-only chain resolution shared by roll and joint weights."""
import json
from contextlib import contextmanager

import bpy
from mathutils import Matrix, Vector, Quaternion

from . import character_setup, finger_bank as bank, finger_definition as definition
from . import finger_symmetry, finger_flex


def owner(context):
    setup = character_setup.settings(context)
    obj = setup.body if setup and setup.body and setup.body.character_designer_finger_bank.survey else bank.active_object(context)
    if not obj or not obj.character_designer_finger_bank.survey:
        raise ValueError('Capture Finger Basic Setup on the character mesh first.')
    rig = setup.rig if setup else None
    if rig is None or rig.type != 'ARMATURE': raise ValueError('Set Main Rig in Character Setup first.')
    if obj.name not in context.scene.objects or rig.name not in context.view_layer.objects:
        raise ValueError('The saved character mesh/rig is unavailable in this scene.')
    linked = {m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object}
    if linked and linked != {rig}: raise ValueError('The captured mesh is associated with a different or multiple armatures.')
    if not linked and setup.body != obj:
        raise ValueError('Confirm this unbound mesh as Body Weight Mesh in Character Setup.')
    return obj, rig


def signature(chain):
    from .finger_bones import _head, _tail
    return [{'name': b.name, 'parent': b.parent.name if b.parent else '',
             'head': list(_head(b)), 'tail': list(_tail(b)), 'deform': b.use_deform} for b in chain]


def index(rig):
    from .finger_bones import _bone_collection, _finger_key, _side_key
    result = {}
    for bone in _bone_collection(rig):
        token = _finger_key(bone.name)
        if not token or not bone.use_deform: continue
        if bone.name.casefold().startswith(('ctrl', 'mch', 'org', 'c_', 'ik_', 'fk_')): continue
        digit = {'pointer': 'INDEX', 'little': 'PINKY'}.get(token, token.upper())
        side = _side_key(bone.name)
        if side not in {'L', 'R'}: continue
        result.setdefault(digit+'.'+side, []).append(bone)
    return result


def resolve(obj, rig, key, candidates, hint=()):
    from .finger_bones import _head, _tail, _parent_depth
    slot = obj.character_designer_finger_bank.slots.get(key)
    if not slot or not slot.guide.record: raise ValueError('Finger Setup is missing.')
    if slot.error: raise ValueError(slot.error)
    record = json.loads(slot.guide.record)
    body = record.get('body')
    if not body: raise ValueError('Recapture this older finger definition.')
    pool = candidates.get(key, [])
    if not pool: raise ValueError('No deform finger chain with a verified identity was found.')
    roots = [b for b in pool if b.parent not in pool]
    groups = []
    for root in roots:
        chain, current = [], root
        while current:
            chain.append(current)
            children = [b for b in pool if b.parent == current]
            if len(children) > 1: raise ValueError('The deform finger chain branches; choose an unambiguous rig.')
            current = children[0] if children else None
        groups.append(chain)
    if hint:
        chosen = [c for c in groups if set(hint) & {b.name for b in c}]
        if chosen: groups = chosen
    saved = json.loads(slot.bones) if slot.bones else None
    if len(groups) > 1 and saved and saved['rig'] == rig.name:
        groups = [c for c in groups if [b.name for b in c] == [b['name'] for b in saved['chain']]]
    if len(groups) != 1: raise ValueError('Multiple candidate chains: select one deform finger bone as a hint.')
    chain = sorted(groups[0], key=_parent_depth)
    if saved and saved['rig'] == rig.name and signature(chain) != saved['chain']:
        raise ValueError('The saved bone chain changed; recapture/review this finger before writing.')
    if any(getattr(b, 'hide', False) or getattr(b, 'lock', False) for b in chain):
        raise ValueError('A target finger bone is hidden or locked.')
    transform = obj.matrix_world.inverted() @ rig.matrix_world
    root, tip = Vector(body['root']), Vector(body['tip'])
    axis = (tip-root).normalized()
    length, radius = body['length'], body['radius']
    # The regular sleeve can start beyond the first thumb joint. Include only
    # the already volume-verified captured root extension, not arbitrary palm.
    extent = [(Vector(p)-root).dot(axis) for p in record['basis']['path']]
    lower, upper = min(0., min(extent)), max(length, max(extent))
    for b in chain:
        head, tail = transform @ _head(b), transform @ _tail(b)
        if (tail-head).normalized().dot(axis) < .5: raise ValueError('Bone direction disagrees with the captured finger.')
        for p in (head, tail):
            t = (p-root).dot(axis)
            if not lower-length*.3 <= t <= upper+length*.3 or (p-root-axis*t).length > max(radius*2, length*.15):
                raise ValueError(f'{b.name}: named chain is outside the captured finger range; check character/rig identity.')
    if (transform @ _tail(chain[-1])-transform @ _head(chain[0])).dot(axis) < length*.55:
        raise ValueError('The candidate chain does not cover the finger body.')
    for a, b in zip(chain, chain[1:]):
        if (_tail(a)-_head(b)).length > max((_tail(a)-_head(a)).length*.25, 1e-5):
            raise ValueError('Parented finger segments have a large joint gap.')
    return chain


def reflection(obj, rig):
    to_plane = bank.plane(obj) @ obj.matrix_world.inverted() @ rig.matrix_world
    return to_plane.inverted() @ Matrix.Diagonal((-1, 1, 1, 1)) @ to_plane


def opposite_absent(obj, source, bm=None):
    """A failed census alone never proves the opposite geometry is absent."""
    from .finger_detect import reflect
    root, tip = reflect(source['root'], bank.plane(obj)), reflect(source['tip'], bank.plane(obj))
    axis, length = (tip-root).normalized(), (tip-root).length
    own = bm is None
    if own: bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        for v in bm.verts:
            distance = (v.co-root).dot(axis)
            if length*.15 < distance < length*.95 and (v.co-root-axis*distance).length < source['radius']*2:
                return False
        return True
    finally:
        if own: bm.free()


def pair(obj, rig, digit, candidates, hints=(), bm=None):
    b = obj.character_designer_finger_bank
    report = json.loads(b.survey)
    present = [s for s in ('L', 'R') if digit+'.'+s in report['candidates']]
    if not present: raise ValueError('No detected finger body.')
    if len(present) == 1:
        other = 'R' if present[0] == 'L' else 'L'
        # Missing candidate is NOT evidence that a damaged/opposite hand is absent.
        if (any(k.endswith('.'+other) for k in report['candidates']) or candidates.get(digit+'.'+other)
                or not opposite_absent(obj, report['candidates'][digit+'.'+present[0]], bm)):
            raise ValueError('Opposite side exists but cannot be paired; repair/recheck it first.')
    paired_hints = set(hints) | {bpy.utils.flip_name(n) for n in hints}
    chains = {digit+'.'+s: resolve(obj, rig, digit+'.'+s, candidates, paired_hints) for s in present}
    if len(chains) == 2:
        if report['warnings'].get(digit): raise ValueError(report['warnings'][digit])
        left, right = chains[digit+'.L'], chains[digit+'.R']
        if len(left) != len(right): raise ValueError('Opposite chains have different segment counts.')
        from .finger_bones import _head, _tail
        mirror = reflection(obj, rig)
        for a, z in zip(left, right):
            tolerance = max((_tail(a)-_head(a)).length*.25, 1e-5)
            if any((mirror @ p-q).length > tolerance for p, q in ((_head(a), _head(z)), (_tail(a), _tail(z)))):
                raise ValueError('Bone pair disagrees with the mesh Setup symmetry plane.')
        # Verify independently captured sides rather than choosing by last selection.
        normals = []
        for key in (digit+'.L', digit+'.R'):
            slot = b.slots[key]
            r = json.loads(slot.guide.record)
            n = r['basis'].get('normal')
            normals.append(Vector(n)*(1 if slot.guide.flip_bend else -1) if n else None)
        if all(n is not None for n in normals):
            mesh_mirror = bank.plane(obj).inverted() @ Matrix.Diagonal((-1, 1, 1, 1)) @ bank.plane(obj)
            if (mesh_mirror.to_3x3().inverted().transposed() @ normals[0]).normalized().dot(normals[1].normalized()) < .98:
                raise ValueError('The two saved bend directions conflict; review this finger pair.')
    return chains


@contextmanager
def edit_rig(context, rig):
    """Restore caller mode/object/bone selection; no silent pose resets."""
    active = context.view_layer.objects.active
    mode = active.mode if active else 'OBJECT'
    selected = list(context.selected_objects)
    if rig.hide_get() or rig.hide_select: raise ValueError('Unhide/unlock Main Rig before calibration.')
    from .finger_bones import _bone_collection
    saved = {b.name: (b.select if rig.mode == 'EDIT' else rig.pose.bones[b.name].select,
                      getattr(b, 'select_head', False), getattr(b, 'select_tail', False)) for b in _bone_collection(rig)}
    active_bone = _bone_collection(rig).active
    active_name = active_bone.name if active_bone else None
    try:
        if context.object and context.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
        for o in context.selected_objects: o.select_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        yield
    finally:
        if context.object and context.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
        for o in context.selected_objects: o.select_set(False)
        for o in selected: o.select_set(True)
        context.view_layer.objects.active = active
        if active and mode != 'OBJECT': bpy.ops.object.mode_set(mode=mode)
        collection = _bone_collection(rig)
        for bone in collection:
            if bone.name in saved:
                select, head, tail = saved[bone.name]
                if rig.mode == 'EDIT': bone.select, bone.select_head, bone.select_tail = select, head, tail
                else: rig.pose.bones[bone.name].select = select
        collection.active = collection.get(active_name) if active_name else None


def calibrate(context, selected=False, after_pair=None):
    obj, rig = owner(context)
    from .finger_bones import _bone_collection
    hints = [b.name for b in _bone_collection(rig) if (b.select if rig.mode == 'EDIT' else rig.pose.bones[b.name].select)] if context.object == rig and context.mode in {'EDIT_ARMATURE', 'POSE'} else []
    wanted = set()
    if selected:
        for key, chain in index(rig).items():
            if any(b.name in hints for b in chain): wanted.add(key.split('.')[0])
        if not wanted: raise ValueError('Select at least one deform finger bone; Selected never runs All.')
    else: wanted = set(('THUMB', 'INDEX', 'MIDDLE', 'RING', 'PINKY'))
    results = {'success': {}, 'skipped': {}, 'failed': {}}
    # One shared mesh snapshot/remap for all requested records, not ten copies.
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        with edit_rig(context, rig):
            finger_symmetry.guard_rig(rig)
            candidates = index(rig)
            for digit in sorted(wanted):
                old = {}
                try:
                    chains = pair(obj, rig, digit, candidates, hints, bm)
                    planned = []
                    for key, chain in chains.items():
                        slot = obj.character_designer_finger_bank.slots[key]
                        if not slot.guide.confirmed: raise ValueError('Setup is not confirmed.')
                        r = bank.remap_record(json.loads(slot.guide.record), bm)
                        normal = r['basis'].get('normal')
                        if not normal: raise ValueError('Bend side missing: capture a consistent top strip.')
                        bend_world = obj.matrix_world.to_3x3().inverted().transposed() @ Vector(normal)*(1 if slot.guide.flip_bend else -1)
                        bend = rig.matrix_world.to_3x3().inverted() @ bend_world
                        for bone in chain:
                            t, b, axis = finger_flex.frame(bone.tail-bone.head, bend)
                            planned.append((bone.name, t, b, axis))
                    finger_symmetry.guard_pose(rig, [p[0] for p in planned])
                    old = {p[0]: rig.data.edit_bones[p[0]].roll for p in planned}
                    mirror_x = rig.data.use_mirror_x
                    try:
                        rig.data.use_mirror_x = False
                        for name, t, bend, axis in planned:
                            bone = rig.data.edit_bones[name]
                            bone.align_roll(axis.cross(t))
                            delta = Quaternion(bone.x_axis, .05) @ t-t
                            if delta.normalized().dot(bend) < .99: raise ValueError('Positive Local X validation failed.')
                        if after_pair: after_pair(digit)
                    except Exception:
                        for name, roll in old.items(): rig.data.edit_bones[name].roll = roll
                        raise
                    finally: rig.data.use_mirror_x = mirror_x
                    for key, chain in chains.items():
                        obj.character_designer_finger_bank.slots[key].bones = json.dumps({'rig': rig.name, 'chain': signature(chain)})
                    results['success'][digit] = len(planned)
                except (ValueError, RuntimeError) as exc:
                    results['failed' if old else 'skipped'][digit] = str(exc)
            rig.update_tag(refresh={'DATA'})
    finally: bm.free()
    return results
