"""Selected-chain copies which keep the other native animation channels intact.

The original IDs are read only. Only copied Actions' selected-bone curves and
drivers are removed; unrelated curves, slots, NLA, constraints and inputs remain.
"""
import ast
import math

import bpy

from . import animation_retarget as retarget
from . import hair_bones_rig as hair
from . import hair_resegment_channels as channels


class PartialHairError(ValueError):
    pass


_TRANSFORMS = ('location', 'rotation_euler', 'rotation_quaternion',
               'rotation_axis_angle', 'scale')
_STRIP_FIELDS = ('action_frame_start', 'action_frame_end', 'scale', 'repeat',
    'frame_start', 'frame_end', 'blend_type', 'extrapolation', 'influence',
    'mute', 'use_animated_influence', 'use_animated_time', 'use_animated_time_cyclic',
    'strip_time', 'use_auto_blend', 'blend_in', 'blend_out', 'use_reverse',
    'use_sync_length', 'select')


def _bone(path):
    """Read only an anchored native bones[constant-name] path, without eval."""
    if not isinstance(path, str):
        return None
    try:
        tree = ast.parse('owner.' + path, mode='eval')
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        value = node.value
        if not isinstance(value, ast.Attribute) or value.attr != 'bones':
            continue
        base = value.value
        if isinstance(base, ast.Attribute) and base.attr == 'pose':
            base = base.value
        if not isinstance(base, ast.Name) or base.id != 'owner':
            continue
        if isinstance(node.slice, ast.Constant) and type(node.slice.value) is str:
            return node.slice.value
        raise PartialHairError('An animated Hair bone path is not a native constant-name path.')
    return None


def _pairs(owner):
    ad = getattr(owner, 'animation_data', None)
    if ad is None:
        return
    if ad.action is not None:
        yield ad.action, ad.action_slot
    for track in ad.nla_tracks:
        for strip in _strips(track.strips):
            if strip.type == 'CLIP' and strip.action is not None:
                yield strip.action, strip.action_slot


def _strips(strips):
    for strip in strips:
        yield strip
        if strip.type == 'META':
            yield from _strips(strip.strips)


def _bags(action, slot):
    if not action.is_action_layered:
        return [(None, None, action)]
    if slot is None:
        raise PartialHairError('A selected Hair Action has no authenticated native slot.')
    return [(layer, strip, bag) for layer in action.layers for strip in layer.strips
            for bag in getattr(strip, 'channelbags', ()) if bag.slot_handle == slot.handle]


def _driver_depends(curve, rig, selected):
    for variable in curve.driver.variables:
        for target in variable.targets:
            if target.id not in (rig, rig.data):
                continue
            if (getattr(target, 'bone_target', '') in selected
                    or _bone(getattr(target, 'data_path', '')) in selected):
                return True
    if curve.driver.type == 'SCRIPTED' and curve.driver.use_self:
        # Native driver variables are the normal contract. A direct self.bones
        # expression must also be proven not to dereference a removed chain.
        try:
            tree = ast.parse(curve.driver.expression, mode='eval')
        except (SyntaxError, ValueError):
            return False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Attribute):
                continue
            if node.value.attr != 'bones':
                continue
            if not isinstance(node.slice, ast.Constant) or type(node.slice.value) is not str:
                raise PartialHairError('Resolve a dynamic self.bones driver before changing this Hair chain.')
            if node.slice.value in selected:
                return True
    return False


def _constraint_depends(constraint, rig, selected):
    for target_name, bone_name in (('target', 'subtarget'),
                                   ('pole_target', 'pole_subtarget'),
                                   ('space_object', 'space_subtarget')):
        if (getattr(constraint, target_name, None) is rig
                and getattr(constraint, bone_name, '') in selected):
            return True
    return any(getattr(item, 'target', None) is rig and getattr(item, 'subtarget', '') in selected
               for item in getattr(constraint, 'targets', ()))


def validate(source, rig, record, selected_signatures):
    """Refuse ambiguous partial animation/dependencies before creating any IDs."""
    selected_signatures = tuple(selected_signatures)
    by_signature = {chain['signature']: chain for chain in record['chains']}
    if (not selected_signatures or len(selected_signatures) != len(set(selected_signatures))
            or any(signature not in by_signature for signature in selected_signatures)):
        raise PartialHairError('Choose one or more authenticated Hair bone chains.')
    selected_set = set(selected_signatures)
    selected = {name for signature in selected_signatures for name in by_signature[signature]['bones']}
    if record.get('mirror_layout'):
        for chain in record['chains']:
            other = chain.get('mirror_of')
            if other and ((chain['signature'] in selected_set) != (other in selected_set)):
                raise PartialHairError('Select both sides of this native Mirror Hair pair together.')
    ad = rig.animation_data
    if ad is not None:
        if ad.use_tweak_mode:
            raise PartialHairError('Finish NLA Tweak Mode before changing selected Hair chains.')
        if ad.action is not None:
            if (ad.action_blend_type != 'REPLACE'
                    or not math.isclose(ad.action_influence, 1., rel_tol=0, abs_tol=1e-8)
                    or ad.action_extrapolation != 'HOLD'):
                raise PartialHairError('Selected Hair needs its active Action in Replace, full influence and Hold mode.')
            bags = _bags(ad.action, ad.action_slot)
            if len(bags) > 1:
                raise PartialHairError('The active Hair slot spans multiple animation strips; resolve it before changing a chain.')
            for layer, strip, bag in bags:
                if layer is not None and (getattr(layer, 'mute', False)
                        or not math.isclose(getattr(layer, 'influence', 1.), 1., rel_tol=0, abs_tol=1e-8)
                        or getattr(layer, 'mix_mode', 'REPLACE') != 'REPLACE'
                        or strip.type != 'KEYFRAME'):
                    raise PartialHairError('The active Hair Action layer cannot safely receive selected-chain samples.')
    mirror = source.get(hair.MIRROR_KEY)
    owners = [rig, rig.data, source, source.data, source.data.shape_keys]
    if isinstance(mirror, bpy.types.Object):
        owners.append(mirror)
    # Mesh/Key/Mirror outputs will also be copied and have internal driver ID
    # targets redirected. They must not acquire dangling selected-bone inputs.
    for owner in (owner for owner in owners if owner is not None):
        for action, slot in _pairs(owner):
            _bags(action, slot)
        animation = owner.animation_data
        if animation:
            for curve in animation.drivers:
                selected_output = owner in (rig, rig.data) and _bone(curve.data_path) in selected
                if not selected_output and _driver_depends(curve, rig, selected):
                    raise PartialHairError('An unchanged driver depends on a selected Hair bone; resolve that dependency first.')
    objects = [rig, source]
    if isinstance(mirror, bpy.types.Object):
        objects.append(mirror)
    for obj in objects:
        if (obj.parent is rig and obj.parent_type == 'BONE'
                and obj.parent_bone in selected):
            raise PartialHairError('An unchanged Hair object is parented to the selected Hair bone.')
    constraint_owners = [*objects, *(pose for pose in rig.pose.bones if pose.name not in selected)]
    for owner in constraint_owners:
        if any(_constraint_depends(constraint, rig, selected) for constraint in owner.constraints):
            raise PartialHairError('An unchanged constraint depends on a selected Hair bone; resolve that dependency first.')
    for bone in rig.data.bones:
        if bone.name not in selected and bone.parent and bone.parent.name in selected:
            raise PartialHairError('An unchanged bone is parented to the selected Hair chain.')
    return True


def _slot(action, handle):
    if not action.is_action_layered:
        return None
    matches = [slot for slot in action.slots if slot.handle == handle]
    if len(matches) != 1:
        raise PartialHairError('A copied Action lost its associated slot.')
    return matches[0]


def _copy_actions(owner, actions, cache):
    animation = owner.animation_data
    if not animation:
        return
    def independent(action):
        if action not in cache:
            cache[action] = action.copy()
            actions.append(cache[action])
        return cache[action]
    if animation.action:
        handle = animation.action_slot_handle
        copied = independent(animation.action)
        animation.action = copied
        if copied.is_action_layered:
            animation.action_slot = _slot(copied, handle)
    for track in animation.nla_tracks:
        for strip in _strips(track.strips):
            if strip.type != 'CLIP' or not strip.action:
                continue
            state = {field: getattr(strip, field) for field in _STRIP_FIELDS if hasattr(strip, field)}
            handle = strip.action_slot_handle
            copied = independent(strip.action)
            strip.action = copied
            if copied.is_action_layered:
                strip.action_slot = _slot(copied, handle)
            for field, value in state.items():
                if getattr(strip, field) != value:
                    setattr(strip, field, value)
            if any(getattr(strip, field) != value for field, value in state.items()):
                raise PartialHairError('Copying an Action changed its NLA mapping; the original is retained.')


def _prune(owner, selected):
    seen = set()
    for action, slot in _pairs(owner):
        marker = action.as_pointer(), slot.handle if slot else None
        if marker in seen:
            continue
        seen.add(marker)
        for _, _, bag in _bags(action, slot):
            for curve in tuple(bag.fcurves):
                if _bone(curve.data_path) in selected:
                    bag.fcurves.remove(curve)
    animation = owner.animation_data
    if animation:
        for curve in tuple(animation.drivers):
            if _bone(curve.data_path) in selected:
                animation.drivers.remove(curve)


def _redirect_constraints(owner, mapping):
    for constraint in owner.constraints:
        for prop in constraint.bl_rna.properties:
            if prop.type != 'POINTER' or prop.is_readonly or prop.is_runtime:
                continue
            target = getattr(constraint, prop.identifier, None)
            if isinstance(target, bpy.types.ID) and target in mapping:
                setattr(constraint, prop.identifier, mapping[target])
        for item in getattr(constraint, 'targets', ()):
            if item.target in mapping:
                item.target = mapping[item.target]


def prepare_copy(source_rig, copy_rig, source, clone, actions, selected_names):
    """Copy and prune only selected paths; return transient restoration state."""
    selected_names = frozenset(selected_names)
    mapping = {source_rig: copy_rig, source_rig.data: copy_rig.data,
               source: clone, source.data: clone.data}
    if source.data.shape_keys:
        mapping[source.data.shape_keys] = clone.data.shape_keys
    old_mirror, new_mirror = source.get(hair.MIRROR_KEY), clone.get(hair.MIRROR_KEY)
    if isinstance(old_mirror, bpy.types.ID) and isinstance(new_mirror, bpy.types.ID) and old_mirror is not new_mirror:
        mapping[old_mirror] = new_mirror
    cache = {}
    copied_owners = tuple(dict.fromkeys(mapping.values()))
    for owner in copied_owners:
        if not hasattr(owner, 'animation_data'):
            continue
        _copy_actions(owner, actions, cache)
    for owner in (copy_rig, copy_rig.data):
        _prune(owner, selected_names)
    for original, copied in mapping.items():
        animation = getattr(copied, 'animation_data', None)
        if animation:
            for curve in animation.drivers:
                for variable in curve.driver.variables:
                    for target in variable.targets:
                        if target.id in mapping:
                            target.id = mapping[target.id]
    for copied in copied_owners:
        if not isinstance(copied, bpy.types.Object):
            continue
        if copied.parent in mapping:
            inverse, basis = copied.matrix_parent_inverse.copy(), copied.matrix_basis.copy()
            copied.parent = mapping[copied.parent]
            copied.matrix_parent_inverse, copied.matrix_basis = inverse, basis
        _redirect_constraints(copied, mapping)
        if copied.type == 'ARMATURE':
            for pose in copied.pose.bones:
                _redirect_constraints(pose, mapping)
    return dict(mapping=mapping, rig=copy_rig, selected_old=selected_names,
                selected_new=frozenset(), author_pose=None, checkpoint=None)


def _mapped_checkpoint(state, original):
    records = []
    for previous in original['records']:
        owner = state['mapping'].get(previous['owner'])
        if owner is None or _bone(previous['path']) in state['selected_old']:
            continue
        target = channels._resolve(owner, previous['tokens'], previous['index'], writable=True)
        record = dict(previous)
        record.update(owner=owner, owner_pointer=owner.as_pointer(),
            owner_uid=getattr(owner, 'session_uid', None), identity=target['identity'],
            axis=target['axis'], actual_id_pointer=target['actual_id'].as_pointer(),
            size=target['size'], sources=[])
        records.append(record)
    return dict(records=tuple(records))


def _driver_axes(rig):
    ad = rig.animation_data
    identities = set()
    for curve in ad.drivers if ad else ():
        try:
            target = channels._resolve(rig, channels._tokens(curve.data_path),
                                       curve.array_index, writable=False)
            identities.add(target['identity'])
        except (ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            # A natively invalid unrelated driver has no writable output here.
            continue
    return identities


def _driven(rig, path, axis, identities):
    return channels._resolve(rig, channels._tokens(path), axis,
                             writable=False)['identity'] in identities


def _restore_pose(state):
    rig, saved = state['rig'], state['author_pose']
    if saved is None:
        return
    driven = _driver_axes(rig)
    for field, values in saved[0].items():
        current = getattr(rig, field)
        for axis, value in enumerate(values):
            if not _driven(rig, field, axis, driven) and current[axis] != value:
                current[axis] = value
    excluded = state['selected_old'] | state['selected_new']
    for name, fields in saved[1].items():
        if name in excluded or name not in rig.pose.bones:
            continue
        pose = rig.pose.bones[name]
        for field, values in fields.items():
            current = getattr(pose, field)
            path = pose.path_from_id(field)
            for axis, value in enumerate(values):
                if not _driven(rig, path, axis, driven) and current[axis] != value:
                    current[axis] = value


def _verify_pose(state):
    rig, saved = state['rig'], state['author_pose']
    if saved is None:
        return True
    driven = _driver_axes(rig)
    rows = [(rig, saved[0])]
    excluded = state['selected_old'] | state['selected_new']
    rows.extend((rig.pose.bones[name], fields) for name, fields in saved[1].items()
                if name not in excluded and name in rig.pose.bones)
    for owner, fields in rows:
        for field, values in fields.items():
            current = getattr(owner, field)
            path = field if owner is rig else owner.path_from_id(field)
            for axis, value in enumerate(values):
                if not _driven(rig, path, axis, driven) and not math.isclose(
                        current[axis], value, rel_tol=1e-7, abs_tol=1e-7):
                    raise PartialHairError('An unchanged copied Hair transform could not be restored: ' + path)
    return True


def restore_copy(context, copy_state, author_channels):
    """Restore copied, unchanged authors using their original current values."""
    if not copy_state:
        return True
    if copy_state.get('checkpoint') is None:
        copy_state['checkpoint'] = _mapped_checkpoint(copy_state, author_channels)
    _restore_pose(copy_state)
    channels.restore(copy_state['checkpoint'])
    context.view_layer.update()
    channels.verify(copy_state['checkpoint'])
    _verify_pose(copy_state)
    return True


def verify_copy(copy_state):
    if copy_state and copy_state.get('checkpoint'):
        channels.verify(copy_state['checkpoint'])
        _verify_pose(copy_state)
    return True


def _active_bag(rig, actions):
    ad = rig.animation_data_create()
    if ad.action is None:
        action = bpy.data.actions.new(rig.name + ' Selected Hair Motion')
        actions.append(action)
        slot, bag = retarget._new_channelbag(action, rig)
        ad.action, ad.action_slot = action, slot
        # An AnimData with no Action can still retain an earlier blend setup.
        # This new Action contains only the explicitly selected new chains.
        ad.action_blend_type, ad.action_influence = 'REPLACE', 1.
        ad.action_extrapolation = 'HOLD'
        return action, bag
    action, slot = ad.action, ad.action_slot
    bags = _bags(action, slot)
    if bags:
        return action, bags[0][2]
    layer = action.layers.new('Selected Hair Motion')
    strip = layer.strips.new(type='KEYFRAME')
    return action, strip.channelbags.new(slot)


def write_action(context, rig, copy_rig, plans, times, actions, author_frame,
                 author_pose, author_channels, *, copy_state=None):
    """Sample only new selected bone transforms into the copied active Action."""
    from . import hair_resegment as service
    if not copy_state:
        raise PartialHairError('Selected Hair lost its copied animation restoration state.')
    copy_state['author_pose'] = author_pose
    copy_state['selected_new'] = frozenset(name for plan in plans for name in plan['names'])
    copy_state['checkpoint'] = _mapped_checkpoint(copy_state, author_channels)
    action, bag = _active_bag(copy_rig, actions)
    values, previous_q = {}, {}
    selected = [copy_rig.pose.bones[name] for plan in plans for name in plan['names']]
    selected.sort(key=lambda pose: len(pose.parent_recursive))
    for frame in times:
        context.scene.frame_set(math.floor(frame), subframe=frame - math.floor(frame))
        if abs(frame - author_frame) < 1e-7:
            service._restore_author_pose(rig, author_pose)
            channels.restore(author_channels)
            restore_copy(context, copy_state, author_channels)
        context.view_layer.update()
        evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
        target = {}
        for plan in plans:
            old = [evaluated.pose.bones[name] for name in plan['old']['bones']]
            points = [tuple(pose.head) for pose in old] + [tuple(old[-1].tail)]
            positions = [index / len(plan['names']) for index in range(len(plan['names']) + 1)]
            sampled = service.maths.sample_polyline(points, positions, distances=plan['distances'])
            for index, name in enumerate(plan['names']):
                midpoint = (index + .5) / len(plan['names']) * plan['distances'][-1]
                old_index = min(len(old) - 1, max(0, service.bisect.bisect_right(plan['distances'], midpoint) - 1))
                target[name] = service._oriented(service.Vector(sampled[index]),
                    service.Vector(sampled[index + 1]), old[old_index].matrix.to_3x3().col[2],
                    copy_rig.data.bones[name].length)
        for pose in selected:
            pose.rotation_mode = 'QUATERNION'
            pose.matrix = target[pose.name]
            context.view_layer.update()
            q = pose.rotation_quaternion.copy()
            if pose.name in previous_q and q.dot(previous_q[pose.name]) < 0.:
                q.negate()
            pose.rotation_quaternion = q
            previous_q[pose.name] = q
            for field, vector in (('location', pose.location), ('rotation_quaternion', q), ('scale', pose.scale)):
                for axis, number in enumerate(vector):
                    if not math.isfinite(number):
                        raise PartialHairError('Selected Hair sampling produced a non-finite transform.')
                    values.setdefault((pose.path_from_id(field), axis), []).append(float(number))
    for (path, axis), numbers in values.items():
        retarget._write_curve(bag, path, axis, times, numbers)
    return action
