"""Explicit, transactional copies of an existing Hair binding at another count.

Current paint and the authenticated current Rest chain are the inputs.  The
original mesh/rig/actions are retained. Changed counts approximate deformation
and motion: integer frames, active Action key subframes and endpoints are sampled.
NLA is evaluated on that grid; no exact between-sample equivalence is promised.
No playback callback, automatic rebind, normalization or old-paint restore runs.
"""
import bisect
import copy
import json
import math
import uuid

import bpy
from mathutils import Matrix, Vector

from . import hair_bones_rig as hair, hair_bones_variants as variants
from . import hair_resegment_math as maths
from . import hair_resegment_channels as channels
from . import hair_strand_registry as registry, hair_motion_profiles as profiles
from . import animation_retarget as retarget

VERSION_KEY = 'character_designer_hair_resegment_v1'
MESH_KEY = 'character_designer_hair_resegment_mesh'
OWNER_KEY = 'character_designer_hair_resegment_owner'
REST_KEY = 'character_designer_hair_resegment_rest_v1'
COPY_KEY = 'character_designer_hair_resegment_copy'


class HairResegmentError(ValueError):
    pass


def is_version(source):
    return isinstance(source, bpy.types.Object) and source.type == 'MESH' and source.get(VERSION_KEY) == 1


def root_source(source):
    root = variants.source_for(source)
    if root is None:
        raise HairResegmentError('Choose an existing bound Hair mesh.')
    return root


def physics_owner(source):
    if not is_version(source):
        return source.get(hair.RIG_KEY)
    rig = source.get(hair.RIG_KEY)
    owner = source.get(OWNER_KEY)
    if (not isinstance(rig, bpy.types.Object) or rig.get(MESH_KEY) is not source
            or not isinstance(owner, bpy.types.Object) or owner.type != 'ARMATURE'
            or rig.get(OWNER_KEY) is not owner
            or root_source(source).get(hair.RIG_KEY) is not owner):
        return None
    return owner


def verify_cleanup(source, rig):
    """Prove every copied bone; the version marker alone grants no deletion."""
    if (not is_version(source) or physics_owner(source) is None
            or source.get(hair.RIG_KEY) is not rig or rig.data.get(COPY_KEY) is not source):
        raise HairResegmentError('A Hair version lost its copied rig ownership; cleanup stopped.')
    try:
        saved = json.loads(source[REST_KEY])
        if set(saved) != set(rig.data.bones.keys()):
            raise ValueError()
        for bone in rig.data.bones:
            record = saved[bone.name]
            if (bone.get(COPY_KEY) is not source or bone.use_deform != record['deform']
                    or bone.inherit_scale != record['inherit_scale']
                    or not hair._bone_state_matches(hair._bone_state(bone), record['rest'])):
                raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise HairResegmentError('The Hair version copied Rest/bone inventory changed; cleanup stopped.') from None
    return True


def owned_actions(source):
    rig = source.get(hair.RIG_KEY)
    uid = hair._read_records(source)['source_id']
    result = set()
    for owner in (source, source.data, source.data.shape_keys, rig, rig.data):
        ad = owner.animation_data if owner else None
        if ad:
            if ad.action and ad.action.get(COPY_KEY) == uid:
                result.add(ad.action)
            result.update(strip.action for track in ad.nla_tracks for strip in track.strips
                          if strip.action and strip.action.get(COPY_KEY) == uid)
    return result


def active_sources(context, owner, sources):
    """Select one visible version per source; ambiguous visibility is refused."""
    families = {}
    for source in sources:
        if physics_owner(source) is not owner:
            raise HairResegmentError('A Hair version lost its character ownership.')
        families.setdefault(root_source(source), []).append(source)
    result = []
    for family in families.values():
        visible = [source for source in family if source.visible_get(view_layer=context.view_layer)]
        if len(visible) > 1:
            raise HairResegmentError('Show only one version of this Hair before starting physics.')
        result.extend(visible)
    return tuple(result)


def guard_switch(context, source):
    """Do not silently stop a solver or discard an unresolved recovery marker."""
    from . import hair_wiggle2_adapter as wiggle
    root = root_source(source)
    family = [root] + [c.get(variants.MESH_KEY) for c in variants.variants_for(root)]
    for item in family:
        if not isinstance(item, bpy.types.Object):
            continue
        state = wiggle.preview_state(item)
        if state['active'] or state.get('pending_recovery') or not state['stopped_confirmed']:
            raise HairResegmentError('Stop Hair preview and resolve its recovery before changing Hair versions.')


def _times(rig, scene, start, end):
    ad = rig.animation_data
    actions = []
    if ad:
        if ad.use_tweak_mode:
            raise HairResegmentError('Finish NLA Tweak Mode before sampling Hair motion.')
        if ad.action:
            actions.append((ad.action, ad.action_slot))
        for track in ad.nla_tracks:
            for strip in track.strips:
                if strip.type != 'CLIP':
                    raise HairResegmentError('Hair sampling supports ordinary NLA Action clips only.')
                if strip.action:
                    actions.append((strip.action, strip.action_slot))
    if start is None:
        start = min([float(scene.frame_current_final)] + [float(a.frame_range[0]) for a, _ in actions]
                    + [float(s.frame_start) for t in ad.nla_tracks for s in t.strips] if ad else [scene.frame_current_final])
    if end is None:
        end = max([float(scene.frame_current_final)] + [float(a.frame_range[1]) for a, _ in actions]
                  + [float(s.frame_end) for t in ad.nla_tracks for s in t.strips] if ad else [scene.frame_current_final])
    if (isinstance(start, bool) or isinstance(end, bool) or not math.isfinite(float(start))
            or not math.isfinite(float(end)) or float(end) < float(start)):
        raise HairResegmentError('Choose a finite, ordered animation range.')
    start, end = float(start), float(end)
    if end - start > 10000:
        raise HairResegmentError('Sample at most 10000 frames per Hair version.')
    values = {start, end, *(float(i) for i in range(math.ceil(start), math.floor(end) + 1))}
    # NLA key coordinates are Action-local, not Scene coordinates. Their visible
    # composition is sampled at integer frames; do not mislabel raw strip keys
    # as Scene subframes (scaled/repeated/reversed strips may map elsewhere).
    direct = [(ad.action, ad.action_slot)] if ad and ad.action else []
    for action, slot in direct:
        values.update(float(k.co.x) for curve in retarget._curves(action, slot)
                      for k in curve.keyframe_points if start <= k.co.x <= end)
    if start <= scene.frame_current_final <= end:
        values.add(float(scene.frame_current_final))
    return tuple(sorted(values))


def _transform_snapshot(obj):
    fields = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')
    return {field: tuple(getattr(obj, field)) for field in fields}


def _author_pose(rig):
    return _transform_snapshot(rig), {p.name: _transform_snapshot(p) for p in rig.pose.bones}


def _restore_author_pose(rig, state):
    for field, value in state[0].items():
        setattr(rig, field, value)
    for name, channels in state[1].items():
        for field, value in channels.items():
            setattr(rig.pose.bones[name], field, value)


def _family_pose(source):
    root = root_source(source)
    meshes = [root] + [c.get(variants.MESH_KEY) for c in variants.variants_for(root)]
    rigs = {m.get(hair.RIG_KEY) for m in meshes if isinstance(m,bpy.types.Object)}
    result = {rig: ('RIG',_author_pose(rig)) for rig in rigs
              if isinstance(rig,bpy.types.Object) and rig.type=='ARMATURE'}
    for mesh in meshes:
        if isinstance(mesh,bpy.types.Object):
            keys=mesh.data.shape_keys
            result[mesh]=('MESH',(_transform_snapshot(mesh),
                {key.name:float(key.value) for key in keys.key_blocks} if keys else {}))
    return result


def _restore_family_pose(context, state):
    for obj, (kind, values) in state.items():
        if bpy.data.objects.get(obj.name) is obj:
            if kind=='RIG':
                _restore_author_pose(obj,values)
            else:
                for field,value in values[0].items():
                    setattr(obj,field,value)
                if obj.data.shape_keys:
                    for name,value in values[1].items():
                        obj.data.shape_keys.key_blocks[name].value=value
    context.view_layer.update()


def show_version(context, source, collection):
    guard_switch(context,source)
    author_channels = channels.capture()
    state = _family_pose(source)
    try:
        return variants.show_variant(context,collection)
    finally:
        try:
            _restore_family_pose(context,state)
        finally:
            channels.restore(author_channels)
            context.view_layer.update()
            channels.verify(author_channels)


def show_original(context, source, *, edit=False):
    guard_switch(context,source)
    author_channels = channels.capture()
    state = _family_pose(source)
    try:
        if edit:
            return variants.show_source(context,source)
        root=root_source(source)
        visibility=variants._initial_visibility(root)
        if visibility['viewport'] or visibility['hidden'] or visibility['select']:
            raise HairResegmentError('Reveal and unlock the original Hair before returning to it.')
        if context.object and context.object.mode!='OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for collection in variants.variants_for(root):
            collection.hide_viewport=collection.hide_render=True
        variants._set_visibility(root,visibility)
        context.view_layer.update()
        hair._mode(context,root,'OBJECT')
        return root
    finally:
        try:
            _restore_family_pose(context,state)
        finally:
            channels.restore(author_channels)
            context.view_layer.update()
            channels.verify(author_channels)


def cleanup_versions(context, source, remove):
    guard_switch(context,source)
    _cleanup_animation_guard(context,source)
    author_channels = channels.capture()
    channels.guard_cleanup(author_channels, context.scene.frame_current_final)
    from . import hair_bones_binding
    inventory = hair_bones_binding._cleanup_inventory(root_source(source))
    deleted_owners = {item.as_pointer() for group in inventory for item in group}
    for mesh in inventory[2]:
        deleted_owners.add(mesh.data.as_pointer())
        if mesh.data.shape_keys:
            deleted_owners.add(mesh.data.shape_keys.as_pointer())
        if is_version(mesh):
            deleted_owners.update(action.as_pointer() for action in owned_actions(mesh))
    deleted_owners.update(rig.data.as_pointer() for rig in inventory[3])
    state = _family_pose(source)
    try:
        return remove(context,source)
    finally:
        # Deleted version objects are intentionally absent; the true character
        # owner keeps current keyed-channel temporary edits after ID re-evaluation.
        alive = {}
        for rig,pose in state.items():
            try:
                if bpy.data.objects.get(rig.name) is rig:
                    alive[rig]=pose
            except ReferenceError:
                pass
        try:
            _restore_family_pose(context,alive)
        finally:
            channels.restore(author_channels, skip_deleted=deleted_owners)
            context.view_layer.update()
            channels.verify(author_channels, skip_deleted=deleted_owners)


def _cleanup_animation_guard(context,source):
    """Native deletion Undo re-evaluates Actions; refuse unsaved channel offsets.

    Creating/switching versions retains those temporary edits. Permanent cleanup
    is optional and cannot silently discard them through native history replay.
    """
    family=_family_pose(source)
    owners=set(family)
    owners.update(o.data for o in family if o.type=='ARMATURE')
    for mesh in [o for o in family if o.type=='MESH']:
        owners.add(mesh.data)
        if mesh.data.shape_keys:
            owners.add(mesh.data.shape_keys)
    for owner in owners:
        ad=owner.animation_data
        if not ad:
            continue
        if ad.drivers:
            raise HairResegmentError('Resolve animation drivers before deleting Hair versions; keep the versions while their current channel state cannot be proved without writes.')
        if ad.use_nla and any(not t.mute and t.strips for t in ad.nla_tracks):
            raise HairResegmentError('Mute NLA before deleting Hair versions so native Undo can preserve the current author channels.')
        if not ad.action:
            continue
        for curve in retarget._curves(ad.action,ad.action_slot):
            if curve.mute:
                continue
            try:
                current=owner.path_resolve(curve.data_path)
                if not isinstance(current,(float,int)):
                    current=current[curve.array_index]
                expected=curve.evaluate(context.scene.frame_current_final)
            except (ValueError,KeyError,IndexError,TypeError):
                raise HairResegmentError('Review unresolved animated channels before deleting Hair versions.') from None
            if not math.isclose(float(current),float(expected),rel_tol=1e-6,abs_tol=1e-6):
                raise HairResegmentError('Key or resolve temporary edits on animated channels before deleting Hair versions; native Undo re-evaluates those channels. Keep the versions until then.')


def _selected_chains(record, signatures):
    if signatures is None:
        return tuple(record['chains'])
    if isinstance(signatures, (str, bytes)):
        raise HairResegmentError('Choose one or more complete Hair strands by their bones.')
    try:
        values = tuple(signatures)
    except TypeError as error:
        raise HairResegmentError('Choose one or more complete Hair strands by their bones.') from error
    known = {chain['signature'] for chain in record['chains']}
    if (not values or any(type(value) is not str or not value for value in values)
            or len(set(values)) != len(values) or not set(values).issubset(known)):
        raise HairResegmentError('The selected Hair strands are empty, duplicated or no longer belong to this source.')
    return tuple(chain for chain in record['chains'] if chain['signature'] in values)


def _preflight(context, source, count, strand_signatures=None):
    if type(count) is not int or not 1 <= count <= 12:
        raise HairResegmentError('Choose 1 to 12 bones per chain.')
    if (not isinstance(source, bpy.types.Object) or source.type != 'MESH'
            or context.view_layer.objects.get(source.name) is not source
            or source.library or source.data.library or source.override_library
            or source.data.override_library or not source.is_editable):
        raise HairResegmentError('Choose local, editable Hair in this View Layer.')
    if context.mode not in {'OBJECT', 'POSE', 'EDIT_MESH'}:
        raise HairResegmentError('Use Object, Pose or Hair Mesh Edit Mode.')
    if context.screen and context.screen.is_animation_playing:
        raise HairResegmentError('Stop playback before sampling a Hair version.')
    from . import wiggle2_runtime
    if any(lease['scene'] == context.scene and lease.get('active') and not lease.get('paused')
           for lease in wiggle2_runtime._LEASES.values()):
        raise HairResegmentError('Pause running Hair/Dress physics before sampling a Hair version.')
    if any(p.identifier.startswith('wiggle_') for p in bpy.types.PoseBone.bl_rna.properties):
        from . import hair_wiggle2_adapter
        backend=hair_wiggle2_adapter._backend()
        wiggle2_runtime._prove_backend(backend)
        wiggle2_runtime._schema(backend)
    if context.mode == 'EDIT_MESH' and (context.edit_object is not source or len(context.objects_in_mode) != 1):
        raise HairResegmentError('Finish editing other meshes first.')
    rig = source.get(hair.RIG_KEY)
    if (not isinstance(rig, bpy.types.Object) or rig.type != 'ARMATURE'
            or rig.library or rig.data.library or rig.override_library or rig.data.override_library
            or not rig.is_editable or rig.mode == 'EDIT' or rig.data.pose_position != 'POSE'
            or context.scene.objects.get(rig.name) is not rig):
        raise HairResegmentError('Hair needs its local recorded Armature in Pose position.')
    guard_switch(context, source)
    record = hair._read_records(source)
    if not record or not record['chains']:
        raise HairResegmentError('Bind Hair before changing its segment count.')
    selected = _selected_chains(record, strand_signatures)
    snapshot = hair._mesh_snapshot(source)
    hair._validate_owned(source, rig, record, snapshot)
    existing = registry.read(source)
    # Strict live binding proof is still required when no motion registry exists.
    live, _, _ = registry._live(source, check_layers=True)
    if existing is None:
        existing = live
    old_profiles = profiles.read(source, registry=existing)
    owned = {name for chain in selected for name in chain['bones']}
    for bone in rig.data.bones:
        if bone.name not in owned and bone.parent and bone.parent.name in owned:
            raise HairResegmentError('A non-Hair bone depends on this chain; detach that dependency before resegmentation.')
    for chain in selected:
        if chain.get('members'):
            raise HairResegmentError('Convert legacy shared strands to independent bindings first.')
        if any(source.vertex_groups[name].lock_weight for name in chain['bones']):
            raise HairResegmentError('Unlock the affected Hair weights before changing segments.')
    if source.constraints or source.animation_data and source.animation_data.drivers:
        raise HairResegmentError('Apply source object constraints/drivers before copying Hair versions.')
    if any(m.type in {'CLOTH','SOFT_BODY','FLUID','PARTICLE_SYSTEM','DYNAMIC_PAINT'} for m in source.modifiers):
        raise HairResegmentError('Remove or apply source simulation modifiers before sampling Hair versions.')
    if strand_signatures is not None:
        from . import hair_resegment_partial
        hair_resegment_partial.validate(source, rig, record,
                                       tuple(chain['signature'] for chain in selected))
    return rig, record, existing, old_profiles


def _cleanup(owned):
    # Dependency order matters: objects release mesh/rig/Action users first.
    order = ['objects', 'collections', 'meshes', 'armatures', 'actions', 'texts']
    for kind in order:
        values = getattr(bpy.data, kind)
        for item in reversed(owned[kind]):
            try:
                pointer=item.as_pointer()
            except ReferenceError:
                continue
            if any(existing.as_pointer()==pointer for existing in values):
                values.remove(item, do_unlink=True)


def _copy(context, source, rig, root, count, actions, owned, strand_signatures=None,
          partial_state=None):
    index = max((int(c.get(variants.INDEX_KEY, 0)) for c in variants.variants_for(root)), default=0) + 1
    label = (f'{root.name} · Selected {count} Segments {index:02d}' if strand_signatures is not None
             else f'{root.name} · {count} Segments {index:02d}')
    collection = bpy.data.collections.new(label)
    owned['collections'].append(collection)
    context.scene.collection.children.link(collection)
    clone = source.copy()
    owned['objects'].append(clone)
    clone.data = source.data.copy()
    owned['meshes'].append(clone.data)
    clone.name = label
    collection.objects.link(clone)
    copy_rig = rig.copy()
    owned['objects'].append(copy_rig)
    copy_rig.data = rig.data.copy()
    owned['armatures'].append(copy_rig.data)
    copy_rig.name = label + ' Rig'
    collection.objects.link(copy_rig)
    for obj in (clone, copy_rig):
        obj.hide_viewport = obj.hide_render = obj.hide_select = False
        obj.hide_set(False)
    # The selected-strand path keeps copied parents, unselected channels and
    # constraints native. The explicit whole-mesh path retains its old bake.
    if strand_signatures is None:
        copy_rig.animation_data_clear()
        copy_rig.data.animation_data_clear()
        for constraint in tuple(copy_rig.constraints):
            copy_rig.constraints.remove(constraint)
        for pose in copy_rig.pose.bones:
            for constraint in tuple(pose.constraints):
                pose.constraints.remove(constraint)
    if any(p.identifier.startswith('wiggle_') for p in bpy.types.PoseBone.bl_rna.properties):
        from . import hair_wiggle2_adapter, wiggle2_runtime
        backend=hair_wiggle2_adapter._backend()
        wiggle2_runtime._prove_backend(backend)
        with wiggle2_runtime._writes(backend):
            copy_rig.wiggle_enable=False
            for pose in copy_rig.pose.bones:
                for field in ('wiggle_head','wiggle_tail'):
                    setattr(pose,field,False)
                # Copied runtime group state belongs to the old rig's solver,
                # never to this new independent version.
                if pose.is_property_set('wiggle'):
                    pose.property_unset('wiggle')
            if copy_rig.is_property_set('wiggle'):
                copy_rig.property_unset('wiggle')
    if strand_signatures is None:
        copy_rig.parent = None
        copy_rig.rotation_mode = 'QUATERNION'
    if strand_signatures is None:
        for data in (clone, clone.data, clone.data.shape_keys):
            if data:
                variants._copy_animation(data, actions)
    variants._redirect_self_drivers(source, clone)
    for key in tuple(clone.keys()):
        if key.startswith('character_designer_hair_') and key not in {
                'character_designer_hair_groups', 'character_designer_hair_group_source',
                'character_designer_hair_group_id', 'character_designer_hair_group_guide'}:
            del clone[key]
    if clone.parent is rig:
        clone.parent = copy_rig
        clone.matrix_parent_inverse = source.matrix_parent_inverse.copy()
        clone.matrix_basis = source.matrix_basis.copy()
    for modifier in clone.modifiers:
        if modifier.type == 'ARMATURE':
            modifier.object = copy_rig
    reference = source.get(hair.MIRROR_KEY)
    if reference is not None:
        new_ref = reference.copy()
        owned['objects'].append(new_ref)
        new_ref.name = label + ' Mirror'
        collection.objects.link(new_ref)
        new_ref[hair.SOURCE_KEY] = clone
        for constraint in new_ref.constraints:
            if getattr(constraint, 'target', None) is rig:
                constraint.target = copy_rig
        for modifier in clone.modifiers:
            if modifier.type == 'MIRROR' and modifier.mirror_object is reference:
                modifier.mirror_object = new_ref
        clone[hair.MIRROR_KEY] = new_ref
    if strand_signatures is not None:
        from . import hair_resegment_partial
        selected_names = {name for chain in _selected_chains(hair._read_records(source), strand_signatures)
                          for name in chain['bones']}
        prepared = hair_resegment_partial.prepare_copy(rig, copy_rig, source, clone, actions, selected_names)
        if partial_state is not None:
            partial_state.update(prepared)
    return collection, clone, copy_rig, index


def _chains(context, source, rig, clone, copy_rig, record, count, strand_signatures=None):
    selected = _selected_chains(record, strand_signatures)
    old_names = {name for chain in selected for name in chain['bones']}
    positions = {chain['signature']: index for index, chain in enumerate(record['chains'])}
    plans = []
    for chain in selected:
        bones = [rig.data.bones[name] for name in chain['bones']]
        points = [tuple(b.head_local) for b in bones] + [tuple(bones[-1].tail_local)]
        distances = maths.cumulative_lengths(points)
        samples = (tuple(points) if len(bones) == count else
                   maths.sample_polyline(points, [i / count for i in range(count + 1)]))
        chain_index = positions[chain.get('mirror_of', chain['signature'])]
        side = '.' + chain['mirror_side'] if record.get('mirror_layout') else ''
        names = [f'Hair {chain_index+1:03d} Segment {i+1:02d}{side}' for i in range(count)]
        plans.append(dict(old=chain, points=points, distances=distances, names=names,
                          samples=samples, lengths=[math.dist(a,b) for a,b in zip(samples,samples[1:])]))
    reserved = set(copy_rig.data.bones.keys()) - old_names
    generated = [name for plan in plans for name in plan['names']]
    if (len(generated) != len(set(generated)) or reserved.intersection(generated)
            or any(clone.vertex_groups.get(name) and name not in old_names for name in generated)):
        raise HairResegmentError('A new Hair segment name is occupied by unrelated bone or painted data.')
    hair._mode(context, copy_rig, 'EDIT')
    for name in old_names:
        copy_rig.data.edit_bones.remove(copy_rig.data.edit_bones[name])
    for plan in plans:
        parent = copy_rig.data.edit_bones.get(record['parent'])
        z = rig.data.bones[plan['old']['bones'][0]].matrix_local.to_3x3().col[2]
        for i, (head, tail) in enumerate(zip(plan['samples'], plan['samples'][1:])):
            bone = copy_rig.data.edit_bones.new(plan['names'][i])
            bone.head, bone.tail = head, tail
            bone.align_roll(z)
            bone.parent = parent
            bone.use_connect = bool(i)
            bone.use_deform = True
            # Each sampled armature-space segment owns its scale. Inheriting a
            # previous chord's nonuniform scale introduces shear and endpoint drift.
            bone.inherit_scale = 'NONE'
            parent = bone
    hair._mode(context, copy_rig, 'OBJECT')
    updated = copy.deepcopy(record)
    updated.update(source_id=uuid.uuid4().hex, armature=copy_rig.name)
    by_signature = {plan['old']['signature']: plan for plan in plans}
    for chain in updated['chains']:
        plan = by_signature.get(chain['signature'])
        if plan is None:
            for name in chain['bones']:
                copy_rig.data.bones[name][hair.SOURCE_KEY] = clone
            continue
        chain.update(bones=plan['names'], count=count, requested_count=count,
                     rest=[hair._bone_state(copy_rig.data.bones[name]) for name in plan['names']])
        for name in plan['names']:
            bone = copy_rig.data.bones[name]
            bone[hair.OWNER_KEY] = hair.OWNER_VALUE
            bone[hair.SOURCE_KEY] = clone
            bone[hair.SIGNATURE_KEY] = chain['signature']
            copy_rig.pose.bones[name].rotation_mode = 'QUATERNION'
    clone[hair.RECORD_KEY] = json.dumps(updated, sort_keys=True, separators=(',', ':'))
    clone[hair.RIG_KEY] = copy_rig
    return plans, updated


def _write_weights(source, clone, rig, plans):
    old_names = {name for p in plans for name in p['old']['bones']}
    names = {g.index: g.name for g in source.vertex_groups}
    # Read current paint from this copy's exact copied vertices, never old bind data.
    rows = [{names[w.group]: float(w.weight) for w in vertex.groups} for vertex in source.data.vertices]
    for name in old_names:
        clone.vertex_groups.remove(clone.vertex_groups[name])
    for plan in plans:
        if any(clone.vertex_groups.get(name) is not None for name in plan['names']):
            raise HairResegmentError('A new Hair bone name is occupied by unrelated painted data.')
        groups = [clone.vertex_groups.new(name=name) for name in plan['names']]
        old_lengths = [b-a for a,b in zip(plan['distances'], plan['distances'][1:])]
        for index, row in enumerate(rows):
            old_weights = [row.get(name, 0.) for name in plan['old']['bones']]
            if not any(old_weights):
                continue
            # The vertex is an exact copy; every old painted influence remains
            # an input. Never replace deliberate paint by a geometric guess.
            weights = maths.remap_weights(old_weights, old_lengths, plan['lengths'])
            if any(weight>1. for weight in weights):
                raise HairResegmentError('This segment count cannot represent current painted weights above one per bone; keep more segments or edit that paint explicitly.')
            for group, weight in zip(groups, weights):
                if weight > 0.:
                    group.add([index], weight, 'REPLACE')


def _oriented(head, tail, z_hint, rest_length):
    y = tail - head
    length = y.length
    if length <= 1e-9:
        raise HairResegmentError('A sampled Hair segment collapsed; the original is retained.')
    y.normalize()
    z = z_hint - y * z_hint.dot(y)
    if z.length <= 1e-9:
        z = y.orthogonal()
    z.normalize()
    x = y.cross(z).normalized()
    z = x.cross(y).normalized()
    matrix = Matrix.Identity(4)
    matrix.col[0].xyz = x
    matrix.col[1].xyz = y * (length / rest_length)
    matrix.col[2].xyz = z
    matrix.translation = head
    return matrix


def _write_action(context, source_rig, copy_rig, plans, times, actions, author_frame, author_pose,
                  author_channels):
    action = bpy.data.actions.new(copy_rig.name + ' Sampled Motion')
    actions.append(action)
    slot, bag = retarget._new_channelbag(action, copy_rig)
    owned = {name for p in plans for name in p['old']['bones']}
    values = {}
    previous_q = {}
    ordered = sorted(copy_rig.pose.bones, key=lambda p: len(p.parent_recursive))
    for frame in times:
        context.scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
        if abs(frame - author_frame) < 1e-7:
            _restore_author_pose(source_rig, author_pose)
            channels.restore(author_channels)
        context.view_layer.update()
        evaluated = source_rig.evaluated_get(context.evaluated_depsgraph_get())
        copy_rig.matrix_world = evaluated.matrix_world.copy()
        target = {p.name: p.matrix.copy() for p in evaluated.pose.bones if p.name not in owned}
        for plan in plans:
            bones = [evaluated.pose.bones[name] for name in plan['old']['bones']]
            points = [tuple(p.head) for p in bones] + [tuple(bones[-1].tail)]
            samples = maths.sample_polyline(points, [i / len(plan['names']) for i in range(len(plan['names'])+1)],
                                             distances=plan['distances'])
            for i, name in enumerate(plan['names']):
                t = (i + .5) / len(plan['names']) * plan['distances'][-1]
                old_index = min(len(bones)-1, max(0,bisect.bisect_right(plan['distances'],t)-1))
                target[name] = _oriented(Vector(samples[i]), Vector(samples[i+1]),
                    bones[old_index].matrix.to_3x3().col[2], copy_rig.data.bones[name].length)
        for pose in ordered:
            pose.rotation_mode = 'QUATERNION'
            pose.matrix = target[pose.name]
            # Force parent evaluation before converting the child's armature matrix.
            context.view_layer.update()
        sampled_channels = [('location', copy_rig.location), ('rotation_quaternion', copy_rig.rotation_quaternion),
                            ('scale', copy_rig.scale)]
        for pose in ordered:
            q = pose.rotation_quaternion.copy()
            if pose.name in previous_q and q.dot(previous_q[pose.name]) < 0.:
                q.negate()
            pose.rotation_quaternion = q
            previous_q[pose.name] = q
            sampled_channels += [(pose.path_from_id('location'), pose.location),
                                 (pose.path_from_id('rotation_quaternion'), q),
                                 (pose.path_from_id('scale'), pose.scale)]
        for path, vector in sampled_channels:
            for axis, number in enumerate(vector):
                if not math.isfinite(number):
                    raise HairResegmentError('Hair motion sampling produced a non-finite transform.')
                values.setdefault((path, axis), []).append(float(number))
    for (path, axis), numbers in values.items():
        retarget._write_curve(bag, path, axis, times, numbers)
    ad = copy_rig.animation_data_create()
    ad.action, ad.action_slot = action, slot
    return action


def _selected_registry(clone, previous):
    """Carry proven strand settings forward without guessing new mirror pairs."""
    configured, record, geometry = registry._live(clone, check_layers=True)
    if configured['topology'] != previous['topology']:
        raise HairResegmentError('The copied Hair topology no longer matches its current strand settings.')
    old_by_id = {strand['strand_id']: strand for strand in previous['strands']}
    old_by_signature = {strand['signature']: strand for strand in previous['strands']}
    current = {strand['signature']: strand for strand in configured['strands']}
    if set(current) != set(old_by_signature):
        raise HairResegmentError('The copied Hair strand inventory changed during the operation.')
    configured['diagnostics'] = list(previous['diagnostics'])
    for strand in configured['strands']:
        old = old_by_signature[strand['signature']]
        if strand['layers'] != old['layers'] or strand['vertices'] != old['vertices']:
            raise HairResegmentError('A Hair strand lost its exact copied source layers or vertex identity.')
        strand['order'] = old['order']
    if record.get('mirror_layout'):
        # An unsaved-registry source has only NONE fallback records. Native
        # Mirror ownership still comes from the existing authenticated binding.
        registry._mirror_provenance(configured, record, clone)
    else:
        dropped = set()
        for old in previous['strands']:
            if old['pair_proof'] == 'NONE':
                continue
            partner = old_by_id[old['mirror_id']]
            strand, other = current[old['signature']], current[partner['signature']]
            if not registry._compatible(strand, other):
                key = tuple(sorted((old['signature'], partner['signature'])))
                if key not in dropped:
                    configured['diagnostics'].append(
                        'Changed segment counts no longer match this retained pair: ' + ', '.join(key))
                    dropped.add(key)
                continue
            strand.update(pair_id=registry._pair_id(configured['source_uid'],
                              strand['strand_id'], other['strand_id']),
                          mirror_id=other['strand_id'], side=old['side'],
                          pair_proof=old['pair_proof'],
                          vertex_map=copy.deepcopy(old['vertex_map']),
                          boundary_map=copy.deepcopy(old.get('boundary_map', [])))
    configured['strands'].sort(key=lambda strand: strand['order'])
    registry._validate_links(configured)
    registry._validate_mesh_pairs(configured, geometry)
    return registry._commit(clone, configured)


def _motion_settings(source, clone, old_registry, old_profiles, *, selected=False):
    configured = (_selected_registry(clone, old_registry) if selected
                  else registry.initialize(clone))
    if old_profiles:
        old = {s['signature']: old_profiles['strands'][s['strand_id']] for s in old_registry['strands']}
        settings = copy.deepcopy(old_profiles)
        settings.update(source_uid=configured['source_uid'], registry_topology=configured['topology'],
            strands={s['strand_id']: copy.deepcopy(old[s['signature']]) for s in configured['strands']})
        profiles._write(clone, settings, configured)
    return configured


def _publish(context, source, clone, copy_rig, collection, root, index):
    owner = physics_owner(source)
    if owner is None:
        raise HairResegmentError('Hair character ownership no longer resolves.')
    original_visibility = variants._initial_visibility(root)
    for item in (collection, clone, copy_rig):
        item[variants.SOURCE_KEY] = root
        item[variants.MODE_KEY] = 'RESEGMENTED'
        item[variants.VERSION_KEY] = 1
        item[variants.INDEX_KEY] = index
    clone[VERSION_KEY] = 1
    clone[OWNER_KEY] = owner
    copy_rig[MESH_KEY] = clone
    copy_rig[OWNER_KEY] = owner
    copy_rig.data[COPY_KEY] = clone
    for bone in copy_rig.data.bones:
        bone[COPY_KEY] = clone
    clone[REST_KEY] = json.dumps({bone.name: dict(rest=hair._bone_state(bone),
        deform=bool(bone.use_deform), inherit_scale=bone.inherit_scale)
        for bone in copy_rig.data.bones}, sort_keys=True, separators=(',', ':'))
    collection[variants.MESH_KEY] = clone
    collection[variants.ARMATURE_KEY] = copy_rig
    collection[variants.VISIBILITY_KEY] = json.dumps(original_visibility, sort_keys=True)


def create(context, source, *, bone_count=6, frame_start=None, frame_end=None, activate=True,
           strand_signatures=None):
    rig, record, old_registry, old_profiles = _preflight(context, source, bone_count, strand_signatures)
    selected_signatures = tuple(chain['signature'] for chain in _selected_chains(record, strand_signatures))
    # frame_set evaluates the whole Main, including unrelated current edits.
    # Prove every authored animated leaf before creating any temporary IDs.
    author_channels = channels.capture()
    times = _times(rig, context.scene, frame_start, frame_end)
    root = root_source(source)
    family_pose = _family_pose(source)
    state = variants._context_snapshot(context, source, rig)
    visible = variants._visibility_snapshot(root)
    frame = context.scene.frame_current, context.scene.frame_subframe
    author_pose = _author_pose(rig)
    auto = context.scene.tool_settings.use_keyframe_insert_auto
    actions = []
    partial_state = {} if strand_signatures is not None else None
    owned = {kind:[] for kind in ('objects','collections','meshes','armatures','texts')}
    owned['actions']=actions
    try:
        context.scene.tool_settings.use_keyframe_insert_auto = False
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        collection, clone, copy_rig, index = _copy(context, source, rig, root, bone_count, actions, owned,
                                                strand_signatures, partial_state)
        plans, updated = _chains(context, source, rig, clone, copy_rig, record, bone_count,
                                 strand_signatures)
        _write_weights(source, clone, rig, plans)
        if strand_signatures is None:
            _write_action(context, rig, copy_rig, plans, times, actions,
                          frame[0] + frame[1], author_pose, author_channels)
        else:
            from . import hair_resegment_partial
            hair_resegment_partial.write_action(context, rig, copy_rig, plans, times, actions,
                                                frame[0] + frame[1], author_pose, author_channels,
                                                copy_state=partial_state)
        for action in actions:
            action[COPY_KEY] = updated['source_id']
            action.use_fake_user = False
        _motion_settings(source, clone, old_registry, old_profiles,
                         selected=strand_signatures is not None)
        _publish(context, source, clone, copy_rig, collection, root, index)
        context.scene.frame_set(frame[0], subframe=frame[1])
        _restore_author_pose(rig, author_pose)
        _restore_family_pose(context,family_pose)
        channels.restore(author_channels)
        if partial_state is not None:
            hair_resegment_partial.restore_copy(context, partial_state, author_channels)
        context.view_layer.update()
        if activate:
            variants.show_variant(context, collection)
            if strand_signatures is not None:
                hair._select_chains(context, copy_rig, tuple(name for plan in plans for name in plan['names']))
        else:
            collection.hide_viewport = collection.hide_render = True
            variants._restore_context(context, source, state)
        channels.restore(author_channels)
        if partial_state is not None:
            hair_resegment_partial.restore_copy(context, partial_state, author_channels)
        context.view_layer.update()
        channels.verify(author_channels)
        return dict(mesh=clone, armature=copy_rig, collection=collection, count=bone_count,
            samples=times, mode='RESEGMENTED', original_retained=True,
            selected_signatures=selected_signatures,
            strand_counts={chain['signature']: chain['count'] for chain in updated['chains']},
            exact_deformation_equivalence=False)
    except Exception as error:
        failures = []
        try:
            _cleanup(owned)
        except Exception as failure:
            failures.append(str(failure))
        try:
            variants._restore_visibility(visible)
            context.scene.frame_set(frame[0], subframe=frame[1])
            _restore_author_pose(rig, author_pose)
            _restore_family_pose(context,family_pose)
            context.view_layer.update()
            variants._restore_context(context, source, state)
        except Exception as failure:
            failures.append(str(failure))
        try:
            channels.restore(author_channels)
            context.view_layer.update()
            channels.verify(author_channels)
        except Exception as failure:
            failures.append(str(failure))
        suffix = ' Recovery needs attention: ' + '; '.join(failures) if failures else ''
        raise HairResegmentError(str(error) + suffix) from error
    finally:
        context.scene.tool_settings.use_keyframe_insert_auto = auto
