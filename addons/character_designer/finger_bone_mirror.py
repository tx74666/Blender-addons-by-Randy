"""Explicit active-finger rest-bone mirroring, independent of mesh topology.

Only existing, unambiguous named deform counterparts are changed. No capture
proof, mesh traversal, preview callback or background work is involved.
Mirrored bones retain the same signed Local X bend behavior as calibration.
"""
import json
import math

import bpy
from bpy.types import Operator

from . import finger_bank as bank, finger_chain as chain
from . import finger_symmetry as symmetry, finger_targets as targets

EPS = 2e-6


def _named_chain(candidates, key):
    pool = candidates.get(key, ())
    if not pool:
        raise ValueError(f'{key}: no existing deform finger chain was found.')
    roots = [bone for bone in pool if bone.parent not in pool]
    if len(roots) != 1:
        raise ValueError(f'{key}: multiple named chains are ambiguous.')
    result, bone = [], roots[0]
    while bone:
        result.append(bone)
        children = [item for item in pool if item.parent == bone]
        if len(children) > 1:
            raise ValueError(f'{key}: the named finger chain branches.')
        bone = children[0] if children else None
    if len(result) != len(pool):
        raise ValueError(f'{key}: the named finger chain is disconnected.')
    for bone in result:
        if bone.hide or getattr(bone, 'lock', False):
            raise ValueError(f'{bone.name}: unhide/unlock the finger bone first.')
        if not all(math.isfinite(value) for point in (bone.head, bone.tail) for value in point):
            raise ValueError(f'{bone.name}: the finger bone coordinates are invalid.')
        if bone.length <= EPS:
            raise ValueError(f'{bone.name}: the finger bone is collapsed.')
    return result


def _reflection(obj, rig):
    transform = targets.reflection(obj, rig)
    if not all(math.isfinite(value) for row in transform for value in row):
        raise ValueError('The mirror plane transform is invalid.')
    linear = transform.to_3x3()
    columns = tuple(linear.col)
    if (abs(linear.determinant()+1) > 1e-5 or
            any(abs(column.length-1) > 1e-5 for column in columns) or
            any(abs(columns[a].dot(columns[b])) > 1e-5 for a, b in ((0, 1), (0, 2), (1, 2)))):
        raise ValueError('The mirror plane has shear; use an orthogonal mirror plane first.')
    return transform, linear


def _plan(obj, rig, source_key):
    other_key = source_key[:-1]+('R' if source_key.endswith('.L') else 'L')
    candidates = targets.index(rig)
    source = _named_chain(candidates, source_key)
    other = _named_chain(candidates, other_key)
    if (len(source) != len(other) or any(
            bpy.utils.flip_name(a.name) != b.name or bpy.utils.flip_name(b.name) != a.name
            for a, b in zip(source, other))):
        raise ValueError('The opposite finger needs the same existing named bone chain. No bones were created.')
    transform, linear = _reflection(obj, rig)
    result = {}
    for bone, mate in zip(source, other):
        result[mate.name] = dict(head=transform @ bone.head, tail=transform @ bone.tail,
                                x=-(linear @ bone.x_axis).normalized(),
                                y=(linear @ bone.y_axis).normalized(),
                                z=(linear @ bone.z_axis).normalized())
    symmetry.guard_pose(rig, [bone.name for bone in source+other])
    # Keep every target's existing parenting/connection. A connected external
    # parent or child must not be dragged by a moved finger endpoint.
    for bone in rig.data.edit_bones:
        if not bone.use_connect or bone.parent is None: continue
        head = result[bone.name]['head'] if bone.name in result else bone.head
        tail = result[bone.parent.name]['tail'] if bone.parent.name in result else bone.parent.tail
        if (bone.name in result or bone.parent.name in result) and (head-tail).length > EPS:
            raise ValueError(f'{bone.name}: mirroring would move an unrelated connected joint.')
    return other_key, result


def _write(rig, planned, before):
    for name in planned: rig.data.edit_bones[name].use_connect = False
    for name, wanted in planned.items():
        bone = rig.data.edit_bones[name]
        bone.head, bone.tail = wanted['head'], wanted['tail']
        # Local X is the bend rotation axis (an axial vector), so its mirrored
        # sign reverses while Y/Z reflect normally. Aligning reflected Z gives
        # that right-handed frame and preserves the same signed Local X bend.
        bone.align_roll(wanted['z'])
    for name in planned: rig.data.edit_bones[name].use_connect = before[name]['connected']


def _verify(rig, before, planned):
    current = chain._snapshot_edit(rig)
    if set(current) != set(before):
        raise ValueError('Bone identities changed while mirroring.')
    for name, previous in before.items():
        actual = current[name]
        if any(actual[field] != previous[field] for field in ('parent', 'connected', 'deform')):
            raise ValueError('Mirroring changed a bone parent or connection.')
        wanted = planned.get(name, previous)
        if any((actual[field]-wanted[field]).length > EPS for field in ('head', 'tail')):
            raise ValueError('Bone mirror position verification failed.')
        if name not in planned:
            if abs(actual['roll']-previous['roll']) > EPS:
                raise ValueError('Mirroring changed an unrelated bone Roll.')
            continue
        bone = rig.data.edit_bones[name]
        if any((getattr(bone, axis+'_axis').normalized()-wanted[axis]).length > 2e-5
               for axis in ('x', 'y', 'z')):
            raise ValueError('Bone mirror axis verification failed.')


def _commit_binding(obj, rig, key, planned):
    slot = obj.character_designer_finger_bank.slots.get(key)
    if slot:
        slot.bones = json.dumps({'rig': rig.name,
                                 'chain': targets.signature([rig.data.edit_bones[name] for name in planned])})


def mirror(context):
    """Mirror the active finger's rest position and axes to its existing mate."""
    obj, rig = targets.owner(context)
    state = obj.character_designer_finger_bank
    digit = state.active.split('.')[0]
    if digit not in bank.detect.DIGITS:
        raise ValueError('Choose the current finger first.')
    source_key = digit+'.'+bank.display_side(state)
    symmetry.guard_rig(rig)
    if not rig.is_editable or not rig.data.is_editable:
        raise ValueError('Use an editable Main Rig before mirroring finger bones.')
    with targets.edit_rig(context, rig):
        other_key, planned = _plan(obj, rig, source_key)
        before = chain._snapshot_edit(rig)
        slot = state.slots.get(other_key)
        binding = slot.bones if slot else None
        mirror_x = rig.data.use_mirror_x
        try:
            rig.data.use_mirror_x = False
            _write(rig, planned, before)
            _verify(rig, before, planned)
            _commit_binding(obj, rig, other_key, planned)
            rig.update_tag(refresh={'DATA'})
        except Exception:
            chain._restore_edit(rig, before)
            if slot: slot.bones = binding
            raise
        finally:
            rig.data.use_mirror_x = mirror_x
    return {'source': source_key, 'target': other_key, 'bones': len(planned)}


class CHARACTERDESIGNER_OT_finger_bone_mirror(Operator):
    bl_idname = 'character_designer.finger_bone_mirror'
    bl_label = 'Mirror Finger Bones'
    bl_description = 'Mirror current finger bones and Roll to existing opposite chain'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = bank.active_object(context)
        try:
            result = mirror(context)
        except (ValueError, RuntimeError, KeyError, IndexError, ReferenceError) as exc:
            if obj: obj.character_designer_finger_bank.bone_status = str(exc)
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        message = f"Mirrored {result['source']} to {result['target']} ({result['bones']} bones and Roll)."
        try:
            if obj: obj.character_designer_finger_bank.bone_status = message
            from . import finger_bone_tools
            finger_bone_tools.invalidate(context)
        except Exception:
            # The bone transaction has committed. A display problem must not
            # report CANCELLED or discard the successful operation's Undo step.
            import logging
            logging.getLogger(__name__).warning('Finger bones mirrored; preview refresh failed.', exc_info=True)
            self.report({'WARNING'}, message+' Preview refresh failed; refresh it manually.')
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


CLASSES = (CHARACTERDESIGNER_OT_finger_bone_mirror,)
