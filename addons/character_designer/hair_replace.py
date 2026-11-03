"""Commit authenticated sampled Hair chains on their existing character IDs.

Sampling and publication of a private version belong to the caller.  This module
owns only the selected native bone/group transaction and its local Action.  It
never swaps Mesh/Armature data, edits retained versions, or uses native Undo for
error recovery.  Reconstructed old selected bones cannot retain native pointers
or their former position in Blender's bone enumeration after a failed commit.
"""
import copy
import hashlib
import json
import math

import bpy
from mathutils import Matrix

from . import animation_retarget as retarget
from . import animation_worklist_revision as revision
from . import animation_worklist_target as target
from . import animation_worklist_fingerprint as fingerprint
from . import bone_collections as collections
from . import control_colors
from . import hair_bones_binding as binding
from . import hair_bones_rig as hair
from . import hair_bones_variants as variants
from . import hair_motion_profiles as profiles
from . import hair_resegment as versions
from . import hair_resegment_channels as channels
from . import hair_resegment_math as maths
from . import hair_resegment_partial as partial
from . import hair_strand_registry as registry
from .selected_bone_weights import _capture_vertex_groups, _restore_vertex_groups


class HairReplaceError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise HairReplaceError(message)


def _plain(value):
    if isinstance(value, bpy.types.ID):
        return ('ID', value.bl_rna.identifier, int(value.as_pointer()))
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, float):
        _require(math.isfinite(value), 'Hair contains a non-finite author value.')
        return value
    if hasattr(value, 'items'):
        return tuple((str(key), _plain(item)) for key, item in value.items())
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_plain(item) for item in value))
    try:
        return tuple(_plain(item) for item in value)
    except TypeError as error:
        raise HairReplaceError('An unsupported Hair author value needs its own editing workflow.') from error


def _scalars(owner):
    return tuple((prop.identifier, _plain(getattr(owner, prop.identifier)))
                 for prop in owner.bl_rna.properties
                 if prop.identifier != 'rna_type'
                 and prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'})


def _mesh_payload(source):
    """Exact native correspondence, including ordered topology and all attributes."""
    data = source.data
    geometry = hair._mesh_snapshot(source)
    attributes = tuple((item.name, item.data_type, item.domain,
                        tuple(_scalars(value) for value in item.data))
                       for item in data.attributes)
    keys = data.shape_keys
    key_payload = None
    if keys is not None:
        key_payload = (keys.use_relative, keys.eval_time, _plain(dict(keys.items())),
            tuple((item.name, item.relative_key.name if item.relative_key else None,
                   item.interpolation, item.mute, item.slider_min, item.slider_max,
                   item.value, item.vertex_group, tuple(tuple(point.co) for point in item.data))
                  for item in keys.key_blocks))
    return (geometry['coordinates'],
            tuple(tuple(item.vertices) for item in data.edges),
            tuple((item.vertex_index, item.edge_index) for item in data.loops),
            tuple((tuple(item.vertices), item.loop_start, item.loop_total,
                   item.material_index, item.use_smooth) for item in data.polygons),
            attributes, key_payload, _plain(tuple(data.materials)), _plain(dict(data.items())))


def _digest(value):
    return hashlib.sha256(repr(value).encode('utf-8')).hexdigest()


def _curve_content(action, slot, excluded=()):
    if action is None:
        return ()
    cache = {}
    return tuple((curve.data_path, curve.array_index,
                  fingerprint._curve_payload(curve, schema_cache=cache))
                 for _, _, bag in partial._bags(action, slot) for curve in bag.fcurves
                 if partial._bone(curve.data_path) not in excluded)


def _animation_input(rig):
    ad = rig.animation_data
    if ad is None:
        return None
    return (int(ad.action.as_pointer()) if ad.action else None, ad.action_slot_handle,
            ad.action_blend_type, ad.action_influence, ad.action_extrapolation, ad.use_nla,
            _curve_content(ad.action, ad.action_slot),
            tuple((_scalars(track), tuple((_scalars(strip),
                   int(strip.action.as_pointer()) if strip.action else None,
                   _curve_content(strip.action, strip.action_slot))
                   for strip in partial._strips(track.strips) if strip.type == 'CLIP'))
                  for track in ad.nla_tracks))


def capture_input(source):
    """Capture a transient pre-sampling identity; do not store this on Blender IDs."""
    _require(isinstance(source, bpy.types.Object) and source.type == 'MESH',
             'Choose the current bound Hair mesh.')
    rig = source.get(hair.RIG_KEY)
    _require(isinstance(rig, bpy.types.Object) and rig.type == 'ARMATURE',
             'Hair has no authenticated character Armature.')
    return dict(source=int(source.as_pointer()), mesh=int(source.data.as_pointer()),
                rig=int(rig.as_pointer()), armature=int(rig.data.as_pointer()),
                keys=int(source.data.shape_keys.as_pointer()) if source.data.shape_keys else None,
                geometry=_digest(_mesh_payload(source)),
                weights=_capture_vertex_groups(source), record=source.get(hair.RECORD_KEY),
                source_matrix=_plain(source.matrix_world), rig_matrix=_plain(rig.matrix_world),
                animation=_digest(_animation_input(rig)))


def _copy_property(value):
    if isinstance(value, bpy.types.ID) or value is None or type(value) in (str, bool, int, float):
        return value
    if hasattr(value, 'items'):
        return {key: _copy_property(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return set(value)
    return tuple(_copy_property(item) for item in value)


def _properties(owner):
    return {key: _copy_property(value) for key, value in owner.items()}


def _property_ui(owner):
    result = {}
    for key in owner.keys():
        try:
            result[key] = _copy_property(owner.id_properties_ui(key).as_dict())
        except (TypeError, RuntimeError):
            # Native property groups have no UI manager; their nested native
            # values are already captured by the property snapshot above.
            _require(hasattr(owner[key], 'items') or isinstance(owner[key], bpy.types.ID),
                     'A selected Hair property has unreadable UI metadata: ' + key)
    return result


def _restore_property_ui(owner, saved):
    for key, value in saved.items():
        owner.id_properties_ui(key).update(**value)


def _rna_value(value):
    if isinstance(value, bpy.types.PoseBone):
        return ('POSE_BONE', value.name)
    if isinstance(value, bpy.types.Bone):
        return ('BONE', value.name)
    if isinstance(value, bpy.types.ID) or value is None:
        return ('POINTER', value)
    return ('VALUE', _copy_property(value))


def _rna_fields(owner):
    result = {}
    for prop in owner.bl_rna.properties:
        name = prop.identifier
        if (name in {'rna_type', 'name', 'matrix', 'matrix_basis', 'matrix_channel'}
                or prop.is_readonly or getattr(prop, 'is_runtime', False)
                or prop.type == 'COLLECTION'):
            continue
        if prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM', 'POINTER'}:
            value = getattr(owner, name)
            if prop.type == 'POINTER':
                _require(value is None or isinstance(value, (bpy.types.ID, bpy.types.Bone,
                                                            bpy.types.PoseBone)),
                         'A selected Hair bone has an unsupported native pointer.')
            result[name] = _rna_value(value)
    return result


def _restore_fields(owner, values, rig):
    # Rotation mode precedes all representations, so unkeyed author components
    # are restored exactly rather than through matrix decomposition.
    ordered = sorted(values, key=lambda name: name != 'rotation_mode')
    for name in ordered:
        kind, value = values[name]
        if kind == 'POSE_BONE':
            value = rig.pose.bones.get(value)
            _require(value is not None, 'A saved Hair pose reference was removed.')
        elif kind == 'BONE':
            value = rig.data.bones.get(value)
            _require(value is not None, 'A saved Hair bone reference was removed.')
        setattr(owner, name, value)


def _bone_states(rig, names, *, destination=None, source=None):
    states = binding._owned_bone_snapshot(rig, names)
    for state in states:
        name = state['name']
        bone, pose = rig.data.bones[name], rig.pose.bones[name]
        _require(not pose.constraints, 'A selected Hair bone has constraints; resolve them before replacement.')
        state['properties'] = _properties(bone)
        state['pose_properties'] = _properties(pose)
        state['property_ui'] = _property_ui(bone)
        state['pose_property_ui'] = _property_ui(pose)
        state['all_bone_fields'] = _rna_fields(bone)
        state['all_pose_fields'] = _rna_fields(pose)
        # The binding snapshot's raw PoseBone pointer can become invalid after
        # deleting a selected bone. The name-based native field snapshot owns it.
        state['pose_fields'].pop('custom_shape_transform', None)
        if destination is not None:
            mapped = []
            for collection in state['collections']:
                candidate = destination.data.collections_all.get(collection.name)
                _require(candidate is not None,
                         'A sampled Hair bone collection is missing on the current character: ' + collection.name)
                mapped.append(candidate)
            state['collections'] = tuple(mapped)
            state['properties'].pop(versions.COPY_KEY, None)
            state['property_ui'].pop(versions.COPY_KEY, None)
            state['properties'][hair.SOURCE_KEY] = source
    return states


def _restore_bones(context, rig, states):
    hair._mode(context, rig, 'EDIT')
    for state in states:
        rest = state['rest']
        bone = rig.data.edit_bones.get(state['name']) or rig.data.edit_bones.new(state['name'])
        _require(bone.name == state['name'], 'Blender renamed a replacement Hair bone.')
        bone.head, bone.tail = rest['head'], rest['tail']
        if 'native_roll' in state:
            bone.roll = state['native_roll']
        else:
            bone.align_roll(Matrix(rest['matrix']).to_3x3().col[2])
    for state in states:
        bone, rest = rig.data.edit_bones[state['name']], state['rest']
        bone.parent = rig.data.edit_bones.get(rest['parent'])
        bone.use_connect = rest['connected']
    hair._mode(context, rig, 'OBJECT')
    for state in states:
        bone, pose = rig.data.bones[state['name']], rig.pose.bones[state['name']]
        for field, value in state['bone_fields'].items():
            setattr(bone, field, value)
        for field, value in state['pose_fields'].items():
            setattr(pose, field, value)
        for key, value in state['properties'].items():
            bone[key] = value
        for key, value in state['pose_properties'].items():
            pose[key] = value
        _restore_property_ui(bone, state['property_ui'])
        _restore_property_ui(pose, state['pose_property_ui'])
        for collection in state['collections']:
            collection.assign(bone)
        pose.matrix_basis = state['pose']
        bone.color.palette = state['color']
        bone.color.custom.normal, bone.color.custom.select, bone.color.custom.active = state['color_custom']
        control_colors.restore_bone_state(pose, state['pose_color_state'])
        _restore_fields(bone, state['all_bone_fields'], rig)
        _restore_fields(pose, state['all_pose_fields'], rig)


def _all_curves(owner):
    return tuple(curve for action, slot in partial._pairs(owner)
                 for _, _, bag in partial._bags(action, slot) for curve in bag.fcurves)


def _dependencies(source, rig, old_names, new_names):
    affected = set(old_names) | set(new_names)
    _require(all(obj is rig for obj in bpy.data.objects if getattr(obj, 'data', None) is rig.data),
             'Make the character Armature data single-user before replacing Hair.')
    _require(all(obj is source for obj in bpy.data.objects if getattr(obj, 'data', None) is source.data),
             'Make the Hair Mesh data single-user before replacing Hair.')
    for bone in rig.data.bones:
        _require(not (bone.name not in old_names and bone.parent and bone.parent.name in old_names),
                 'An unchanged bone is parented to a selected Hair bone.')
        if bone.name not in old_names:
            _require(not any(getattr(bone, field, None) is not None
                             and getattr(bone, field).name in affected
                             for field in ('bbone_custom_handle_start', 'bbone_custom_handle_end')),
                     'An unchanged B-Bone uses a selected Hair bone as its handle.')
            handle = rig.pose.bones[bone.name].custom_shape_transform
            _require(handle is None or handle.name not in affected,
                     'An unchanged widget uses a selected Hair bone as its transform.')
    for modifier in source.modifiers:
        _require(not any(prop.type == 'STRING' and 'vertex_group' in prop.identifier
                         and getattr(modifier, prop.identifier) in affected
                         for prop in modifier.bl_rna.properties),
                 'A Hair modifier masks its result with a selected old weight group.')
    keys = source.data.shape_keys
    _require(keys is None or not any(item.vertex_group in affected for item in keys.key_blocks),
             'A Shape Key uses a selected old Hair weight group as its mask.')
    for owner in channels._ids():
        animation = getattr(owner, 'animation_data', None)
        for curve in animation.drivers if animation else ():
            _require(not (owner in (rig, rig.data) and partial._bone(curve.data_path) in affected),
                     'A selected Hair bone has a driver; resolve it before replacement.')
            _require(not partial._driver_depends(curve, rig, affected),
                     'A driver depends on the old or new Hair bone names; resolve it before replacement.')
    for obj in bpy.data.objects:
        _require(not (obj.parent is rig and obj.parent_type == 'BONE' and obj.parent_bone in affected),
                 'An object is parented to a selected Hair bone: ' + obj.name)
        if obj is not source and obj.type == 'MESH' and any(
                item.type == 'ARMATURE' and item.object is rig for item in obj.modifiers):
            groups = {group.index for group in obj.vertex_groups if group.name in affected}
            _require(not any(value.group in groups and value.weight != 0.
                             for vertex in obj.data.vertices for value in vertex.groups),
                     'Another mesh has weights for the selected Hair bones: ' + obj.name)
        owners = (obj, *obj.pose.bones) if obj.type == 'ARMATURE' else (obj,)
        for owner in owners:
            _require(not any(partial._constraint_depends(item, rig, affected) for item in owner.constraints),
                     'A constraint depends on the selected Hair bones: ' + obj.name)
    _require(not any(partial._bone(curve.data_path) in affected for curve in _all_curves(rig.data)),
             'Selected Hair Armature-data animation needs its own migration before replacement.')
    ad = rig.animation_data
    for track in ad.nla_tracks if ad else ():
        for strip in partial._strips(track.strips):
            if strip.type == 'CLIP' and strip.action:
                _require(not any(partial._bone(curve.data_path) in affected
                                 for _, _, bag in partial._bags(strip.action, strip.action_slot)
                                 for curve in bag.fcurves),
                         'The NLA stack animates selected Hair bones; resolve that stack before replacement.')


def _groups_by_name(states):
    return {state['name']: state for state in states}


def _paint(source, version, record, updated, signatures):
    before, after = _capture_vertex_groups(source), _capture_vertex_groups(version)
    old = {chain['signature']: chain for chain in record['chains']}
    new = {chain['signature']: chain for chain in updated['chains']}
    old_names = {name for key in signatures for name in old[key]['bones']}
    new_names = {name for key in signatures for name in new[key]['bones']}
    first, second = _groups_by_name(before), _groups_by_name(after)
    _require(set(first) - old_names == set(second) - new_names,
             'The sampled Hair has different unselected painted groups.')
    for name in set(first) - old_names:
        _require(first[name]['weights'] == second[name]['weights']
                 and first[name]['lock_weight'] == second[name]['lock_weight'],
                 'Current unselected Hair paint differs from the sampled version: ' + name)
    rows = {name: dict(state['weights']) for name, state in first.items()}
    actual = {name: dict(state['weights']) for name, state in second.items()}
    old_rig, new_rig = source[hair.RIG_KEY], version[hair.RIG_KEY]
    for key in signatures:
        a, b = old[key], new[key]
        old_lengths = [old_rig.data.bones[name].length for name in a['bones']]
        new_lengths = [new_rig.data.bones[name].length for name in b['bones']]
        for index in range(len(source.data.vertices)):
            values = [rows[name].get(index, 0.) for name in a['bones']]
            expected = maths.remap_weights(values, old_lengths, new_lengths) if any(values) else [0.] * len(b['bones'])
            _require(all(abs(value - actual[name].get(index, 0.)) <= 2.e-6
                         for name, value in zip(b['bones'], expected)),
                     'Current Hair paint differs from the sampled version; sample the current paint again.')
            _require(abs(math.fsum(values) - math.fsum(actual[name].get(index, 0.)
                         for name in b['bones'])) <= 2.e-6,
                     'The sampled Hair no longer preserves the current selected painted influence.')
    return before, tuple(second[name] for chain in updated['chains'] if chain['signature'] in signatures
                         for name in chain['bones'])


def _write_groups(source, old_names, new_groups):
    for name in old_names:
        source.vertex_groups.remove(source.vertex_groups[name])
    for state in new_groups:
        group = source.vertex_groups.new(name=state['name'])
        _require(group.name == state['name'], 'Blender renamed a replacement Hair weight group.')
        for index, weight in state['weights']:
            group.add((index,), weight, 'REPLACE')
        group.lock_weight = state['lock_weight']


def _active_state(rig):
    ad = rig.animation_data
    return None if ad is None else dict(action=ad.action, slot=ad.action_slot_handle,
        blend=ad.action_blend_type, influence=ad.action_influence,
        extrapolation=ad.action_extrapolation, use_nla=ad.use_nla)


def _restore_active(rig, state):
    if state is None:
        rig.animation_data_clear()
        return
    ad = rig.animation_data_create()
    ad.action = state['action']
    if ad.action is not None and ad.action.is_action_layered:
        ad.action_slot = partial._slot(ad.action, state['slot'])
    ad.action_blend_type, ad.action_influence = state['blend'], state['influence']
    ad.action_extrapolation, ad.use_nla = state['extrapolation'], state['use_nla']


def _prepare_action(rig, version_rig, old_names, new_names):
    ad = rig.animation_data
    action = ad.action.copy() if ad and ad.action else bpy.data.actions.new(rig.name + ' Hair Custom')
    try:
        action.name = rig.name + ' Hair Custom'
        action.use_fake_user = False
        if action.asset_data is not None:
            action.asset_clear()
        for key in (revision.TOKEN, revision.CHECKPOINT, *target._action_motion_keys()):
            action.pop(key, None)
        revision._no_action_ID_properties(dict(action.items()))
        if ad and ad.action:
            slot = partial._slot(action, ad.action_slot_handle)
            bags = partial._bags(action, slot)
            _require(len(bags) <= 1, 'The active Hair slot spans unsupported multiple animation strips.')
            if bags:
                bag = bags[0][2]
            else:
                layer = action.layers.new('Selected Hair Motion')
                bag = layer.strips.new(type='KEYFRAME').channelbags.new(slot)
        else:
            slot, bag = retarget._new_channelbag(action, rig)
        for curve in tuple(bag.fcurves):
            if partial._bone(curve.data_path) in old_names:
                bag.fcurves.remove(curve)
        sampled = version_rig.animation_data
        _require(sampled is not None and sampled.action is not None,
                 'The sampled Hair version has no selected motion Action.')
        bags = partial._bags(sampled.action, sampled.action_slot)
        _require(len(bags) == 1, 'The sampled Hair motion lost its authenticated active slot.')
        curves = [curve for curve in bags[0][2].fcurves if partial._bone(curve.data_path) in new_names]
        expected = {(name, field, index) for name in new_names
                    for field, size in (('location', 3), ('rotation_quaternion', 4), ('scale', 3))
                    for index in range(size)}
        actual = {(partial._bone(curve.data_path), curve.data_path.rsplit('.', 1)[-1], curve.array_index)
                  for curve in curves}
        _require(actual == expected and len(curves) == len(expected),
                 'The sampled Hair motion has incomplete or ambiguous selected transform curves.')
        _require(not any(partial._bone(curve.data_path) in new_names for curve in bag.fcurves),
                 'The current Action already has unrelated curves at the new Hair names.')
        for curve in curves:
            _require(not getattr(curve, 'is_driver', False), 'Sampled Hair transform curves cannot be drivers.')
            _require(all(math.isfinite(number) for point in curve.keyframe_points
                         for vector in (point.co, point.handle_left, point.handle_right) for number in vector)
                     and all(math.isfinite(number) for point in curve.sampled_points for number in point.co),
                     'Sampled Hair motion contains a non-finite transform curve.')
            bag.fcurves.new_from_fcurve(curve)
        return action, slot
    except Exception:
        if action.users == 0:
            bpy.data.actions.remove(action, do_unlink=False)
        raise


def _binding_delta(source, record, signatures, new_by_signature):
    old = binding._read(source)
    expected = {name for chain in record['chains'] for name in chain['bones']}
    _require(set(old['bones']) == expected, 'The reversible Hair binding bone inventory changed.')
    updated = copy.deepcopy(old)
    updated['bones'] = [name for chain in record['chains']
                        for name in (new_by_signature[chain['signature']]['bones']
                                     if chain['signature'] in signatures else chain['bones'])]
    replacements = {name: new_by_signature[chain['signature']]['bones'] for chain in record['chains']
                    if chain['signature'] in signatures for name in chain['bones']}
    affected = []
    for name in old['affected_groups']:
        for replacement in replacements.get(name, (name,)):
            if replacement not in affected:
                affected.append(replacement)
    updated['affected_groups'] = affected
    return updated


def _backup_delta(rig, old_names, new_names):
    if not collections.has_layout_backup(rig):
        return None
    saved = collections._load_backup(rig)
    managed = saved.get('managed', ())
    for item in managed:
        if item.get('name') != 'Hair' or item.get('properties', {}).get(hair.OWNER_KEY) != hair.OWNER_VALUE:
            _require(not set(item.get('bones', ())).intersection(old_names),
                     'A non-Hair managed display record contains selected Hair bones.')
            continue
        values = item['bones']
        if set(values).intersection(old_names):
            item['bones'] = sorted((set(values) - set(old_names)) | set(new_names))
    return saved


def _remove_bones(context, rig, names, capture=None):
    mirror = rig.data.use_mirror_x
    try:
        rig.data.use_mirror_x = False
        hair._mode(context, rig, 'EDIT')
        if capture is not None:
            for state in capture:
                bone = rig.data.edit_bones.get(state['name'])
                _require(bone is not None, 'An original Hair bone disappeared before replacement.')
                state['native_roll'] = float(bone.roll)
        for name in names:
            bone = rig.data.edit_bones.get(name)
            if bone is not None:
                rig.data.edit_bones.remove(bone)
        hair._mode(context, rig, 'OBJECT')
    finally:
        rig.data.use_mirror_x = mirror


def _restore_members(rig, saved):
    for collection, names in saved:
        if tuple(bone.name for bone in collection.bones) == names:
            continue
        for bone in tuple(collection.bones):
            collection.unassign(bone)
        for name in names:
            collection.assign(rig.data.bones[name])


def _restore_saved(owner, values, keys):
    for key in keys:
        if key in values and values[key] is not None:
            owner[key] = values[key]
        else:
            owner.pop(key, None)


def _restore_display(saved):
    for collection, visible, solo, expanded in saved:
        collection.is_visible, collection.is_solo, collection.is_expanded = visible, solo, expanded


def replace_from_version(context, source, version, signatures, activate=True, *,
                         expected_input=None, import_existing=False):
    """Replace selected chains only; existing sampled versions remain untouched."""
    from . import hair_replace_authority as authority
    _require(expected_input is not None or import_existing is True,
             'A pre-sampling Hair identity or explicit existing-version import is required.')
    _require(type(import_existing) is bool and type(activate) is bool,
             'Hair replacement options must be explicit booleans.')
    _require(signatures is not None, 'Choose explicit complete Hair strands for replacement.')
    if expected_input is not None:
        _require(type(expected_input) is dict and capture_input(source) == expected_input,
                 'Current Hair author data changed during sampling; sample it again.')
    _require(not versions.is_version(source) and versions.root_source(source) is source,
             'Choose the original current character Hair for replacement.')
    rig, record, old_registry, old_profiles = versions._preflight(context, source, 1, signatures)
    selected = versions._selected_chains(record, signatures)
    signatures = tuple(chain['signature'] for chain in selected)
    old_names = tuple(name for chain in selected for name in chain['bones'])
    mesh = version.get('mesh') if type(version) is dict else version
    _require(isinstance(mesh, bpy.types.Object) and mesh.type == 'MESH'
             and versions.is_version(mesh) and versions.root_source(mesh) is source
             and versions.physics_owner(mesh) is rig,
             'The sampled Hair version does not belong to this exact character and source.')
    version_rig = mesh.get(hair.RIG_KEY)
    versions.verify_cleanup(mesh, version_rig)
    _require(_mesh_payload(source) == _mesh_payload(mesh),
             'Current Hair topology, coordinates, Keys or attributes differ from the sampled version.')
    _require(_plain(source.matrix_world) == _plain(mesh.matrix_world)
             and _plain(rig.matrix_world) == _plain(version_rig.matrix_world),
             'The sampled Hair coordinate frames differ from the current character.')
    sampled_record = hair._read_records(mesh)
    original = {chain['signature']: chain for chain in record['chains']}
    sampled = {chain['signature']: chain for chain in sampled_record['chains']}
    _require(set(original) == set(sampled) and record['topology'] == sampled_record['topology'],
             'The sampled Hair strand inventory or topology changed.')
    for key, chain in original.items():
        a, b = copy.deepcopy(chain), copy.deepcopy(sampled[key])
        for field in ('bones', 'count', 'requested_count', 'rest'):
            a.pop(field, None)
            b.pop(field, None)
        _require(a == b, 'A sampled Hair chain lost its exact layers or vertex identity.')
        if key not in signatures:
            _require(chain == sampled[key], 'The sampled version changed an unselected Hair chain.')
    new_names = tuple(name for key in signatures for name in sampled[key]['bones'])
    _require(len(new_names) == len(set(new_names)), 'Sampled Hair bone names overlap.')
    partial.validate(mesh, version_rig, sampled_record, signatures)
    _require(not (set(new_names) & (set(rig.data.bones.keys()) - set(old_names))),
             'A replacement Hair bone name is occupied by unrelated data.')
    _require(not (set(new_names) & (set(source.vertex_groups.keys()) - set(old_names))),
             'A replacement Hair weight-group name is occupied by unrelated paint.')
    _dependencies(source, rig, old_names, new_names)
    excluded = set(old_names) | set(new_names)
    source_animation, sampled_animation = rig.animation_data, version_rig.animation_data
    current_curves = _curve_content(source_animation.action, source_animation.action_slot, excluded) if source_animation else ()
    sampled_curves = _curve_content(sampled_animation.action, sampled_animation.action_slot, excluded) if sampled_animation else ()
    _require(current_curves == sampled_curves,
             'The sampled Hair belongs to different unchanged animation; sample the current clip again.')
    old_bones = _bone_states(rig, old_names)
    new_bones = _bone_states(version_rig, new_names, destination=rig, source=source)
    old_groups, new_groups = _paint(source, mesh, record, sampled_record, signatures)
    sampled_registry = registry.read(mesh)
    _require(sampled_registry is not None, 'The sampled Hair has no strict strand registry.')
    by_signature = {item['signature']: item for item in sampled_registry['strands']}
    new_registry = copy.deepcopy(old_registry)
    for item in new_registry['strands']:
        if item['signature'] in signatures:
            sampled_item = by_signature[item['signature']]
            for field in ('bones', 'rest', 'anchor'):
                item[field] = copy.deepcopy(sampled_item[field])
    registry._validate_links(new_registry)
    new_profiles = copy.deepcopy(old_profiles)
    sampled_profiles = profiles.read(mesh, registry=sampled_registry)
    if new_profiles is not None:
        _require(sampled_profiles is not None
                 and new_profiles['group_defaults'] == sampled_profiles['group_defaults'],
                 'Global Hair motion defaults differ; resolve them before selected replacement.')
        for item in new_registry['strands']:
            if item['signature'] in signatures:
                sampled_id = by_signature[item['signature']]['strand_id']
                new_profiles['strands'][item['strand_id']] = copy.deepcopy(sampled_profiles['strands'][sampled_id])
        profiles._validate(new_profiles, new_registry)
    updated = copy.deepcopy(record)
    updated['chains'] = [copy.deepcopy(sampled[chain['signature']])
                         if chain['signature'] in signatures else copy.deepcopy(chain)
                         for chain in record['chains']]
    new_binding = _binding_delta(source, record, signatures, sampled)
    backup = _backup_delta(rig, old_names, new_names)
    snapshot = variants._context_snapshot(context, source, rig)
    members = tuple((item, tuple(bone.name for bone in item.bones)) for item in rig.data.collections_all)
    display = tuple((item, item.is_visible, item.is_solo, item.is_expanded) for item in rig.data.collections_all)
    saved_props = {key: source.get(key) for key in
                   (hair.RECORD_KEY, binding.BINDING_KEY, registry.REGISTRY_KEY, profiles.PROFILE_KEY)}
    saved_backup = {key: _copy_property(rig.data[key]) for key in
                    (collections.BACKUP_KEY, collections.BACKUP_REFS_KEY) if key in rig.data}
    active = _active_state(rig)
    author_channels = channels.capture()
    author_channels['records'] = tuple(item for item in author_channels['records']
        if not (item['owner'] in (rig, rig.data) and partial._bone(item['path']) in old_names))
    plan = authority.prepare(context, rig, old_names)
    action = None
    wrote_bones = False
    auto = context.scene.tool_settings.use_keyframe_insert_auto
    try:
        action, slot = _prepare_action(rig, version_rig, old_names, new_names)
        context.scene.tool_settings.use_keyframe_insert_auto = False
        wrote_bones = True
        _remove_bones(context, rig, old_names, capture=old_bones)
        _restore_bones(context, rig, new_bones)
        # Only newly constructed selected Rest is recorded here. Reuse the
        # existing native reconstruction bound; never recapture surviving Rest.
        by_name = {state['name']: state for state in new_bones}
        for name in new_names:
            _require(hair._bone_state_matches(hair._bone_state(rig.data.bones[name]), by_name[name]['rest']),
                     'A reconstructed Hair bone does not match the sampled native Rest.')
        for chain in updated['chains']:
            if chain['signature'] in signatures:
                chain['rest'] = [hair._bone_state(rig.data.bones[name]) for name in chain['bones']]
                item = next(value for value in new_registry['strands'] if value['signature'] == chain['signature'])
                order = {name: index for index, name in enumerate(chain['bones'])}
                item['rest'] = [registry._rest(rig.data.bones[name], order) for name in chain['bones']]
                item['anchor'] = registry._anchor(rig.data.bones[chain['bones'][0]].parent)
        _write_groups(source, old_names, new_groups)
        source[hair.RECORD_KEY] = json.dumps(updated, sort_keys=True, separators=(',', ':'))
        source[binding.BINDING_KEY] = json.dumps(new_binding, sort_keys=True, separators=(',', ':'))
        if backup is not None:
            collections._write_backup(rig, backup)
            _require(_copy_property(rig.data.get(collections.BACKUP_REFS_KEY, {}))
                     == saved_backup.get(collections.BACKUP_REFS_KEY, {}),
                     'Updating Hair display membership changed unrelated native backup references.')
        animation = rig.animation_data_create()
        animation.action = action
        if action.is_action_layered:
            animation.action_slot = slot
        if active is None or active['action'] is None:
            animation.action_blend_type, animation.action_influence = 'REPLACE', 1.
            animation.action_extrapolation = 'HOLD'
        else:
            animation.action_blend_type, animation.action_influence = active['blend'], active['influence']
            animation.action_extrapolation, animation.use_nla = active['extrapolation'], active['use_nla']
        channels.restore(author_channels)
        channels.verify(author_channels)
        plan['replacement_action'], plan['replacement_slot'] = action, slot.handle if slot else None
        authority.commit(plan, new_names, new_action=action, new_slot=slot.handle if slot else None)
        registry._commit(source, new_registry)
        if new_profiles is not None:
            profiles._write(source, new_profiles, new_registry)
        hair._validate_owned(source, rig, updated, hair._mesh_snapshot(source))
        registry.read(source)
        profiles.read(source, registry=new_registry)
        context.view_layer.update()
        channels.restore(author_channels)
        channels.verify(author_channels)
        if activate:
            hair._mode(context, rig, 'POSE')
            for pose in rig.pose.bones:
                pose.select = pose.name in new_names
            for name in new_names:
                rig.data.bones[name].hide = False
            rig.data.bones.active = rig.data.bones[new_names[0]]
        else:
            mapped = {key: value for key, value in snapshot.items() if key != 'arms'}
            mapped['arms'] = {owner: dict(values) for owner, values in snapshot['arms'].items()}
            if rig in mapped['arms']:
                values = mapped['arms'][rig]
                replacement = {name: sampled[chain['signature']]['bones'] for chain in selected
                               for name in chain['bones']}
                values['selected'] = tuple(dict.fromkeys(name for old in values['selected']
                                                        for name in replacement.get(old, (old,))))
                values['active'] = replacement.get(values['active'], (values['active'],))[0]
            variants._restore_context(context, source, mapped)
        _restore_display(display)
        channels.restore(author_channels)
        return dict(mesh=source, armature=rig, mode='REPLACED', original_retained=False,
                    selected_signatures=signatures,
                    strand_counts={chain['signature']: chain['count'] for chain in updated['chains']},
                    exact_deformation_equivalence=False)
    except Exception as error:
        failures = []
        def recover(label, function):
            try:
                function()
            except Exception as failure:
                failures.append(label + ': ' + str(failure))
        if wrote_bones:
            recover('replacement bones', lambda: _remove_bones(context, rig, new_names))
            recover('original Hair bones', lambda: _restore_bones(context, rig, old_bones))
            recover('original weight groups', lambda: _restore_vertex_groups(source, old_groups))
            recover('native collection membership', lambda: _restore_members(rig, members))
        recover('original Action binding', lambda: _restore_active(rig, active))
        recover('binding and motion records', lambda: _restore_saved(source, saved_props, saved_props))
        recover('display backup records', lambda: _restore_saved(rig.data, saved_backup,
                (collections.BACKUP_KEY, collections.BACKUP_REFS_KEY)))
        recover('author channels', lambda: channels.restore(author_channels))
        recover('Hair authority', lambda: authority.rollback(plan))
        recover('native collection display', lambda: _restore_display(display))
        recover('original UI context', lambda: variants._restore_context(context, source, snapshot))
        if action is not None:
            if action.users == 0 and not action.library:
                recover('owned replacement Action', lambda: bpy.data.actions.remove(action, do_unlink=False))
            else:
                failures.append('The replacement Action gained a user and was retained for recovery.')
        suffix = ' Recovery needs attention: ' + '; '.join(failures) if failures else ''
        failure = HairReplaceError(str(error) + suffix)
        failure.recovery = dict(values_restored=not failures, failures=tuple(failures),
            selected_bone_native_pointers_preserved=False, selected_bone_enumeration_preserved=False)
        raise failure from error
    finally:
        context.scene.tool_settings.use_keyframe_insert_auto = auto
