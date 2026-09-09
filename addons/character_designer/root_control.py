"""Optional global character root, preserving native rest bones and animation inputs."""
from __future__ import annotations

import json
import math
import uuid

import bpy
from mathutils import Matrix, Vector

from .torso_controls import _active, _same_rest, _same_value, _state, _update

OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'root_control'
ROLE_KEY = 'character_designer_root_role'
ID_KEY = 'character_designer_root_id'
RECORD_KEY = 'character_designer_root_control_v1'
ROOT_NAME = 'CTRL_master'
SCALE_PROPERTY = 'uniform_scale'
VERSION = 1


def _limb():
    from . import limb_ik
    return limb_ik


def _error(message):
    return _limb().LimbIKError(message)


def _tag(item, record, role):
    item[OWNER_KEY], item[ID_KEY], item[ROLE_KEY] = OWNER_VALUE, record['id'], role


def _owned(item, record, role):
    return (item is not None and item.get(OWNER_KEY) == OWNER_VALUE
            and item.get(ID_KEY) == record['id'] and item.get(ROLE_KEY) == role)


def get_record(armature):
    raw = armature.data.get(RECORD_KEY)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        if (record['version'] != VERSION or record['master'] != ROOT_NAME
                or record['bones'] != {'MASTER': ROOT_NAME}
                or len(record['controls']) != len(set(record['controls']))
                or not record['sources'] or len(record['sources']) != len(set(record['sources']))
                or set(record['control_states']) != set(record['controls'])
                or set(record['source_states']) != set(record['sources'])
                or set(record['bone_states']) != {'MASTER'}
                or set(record['widgets']) != {'MASTER'}
                or len(record['constraints']) != len(record['sources'])):
            raise ValueError('unsupported structure')
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error('Root Control recovery data is invalid; restore a saved copy.') from exc


def control_name(armature):
    record = get_record(armature)
    if record:
        return record['master']
    bone = armature.data.bones.get(ROOT_NAME)
    if bone and bone.get(_limb().OWNER_KEY) == _limb().OWNER_VALUE and bone.get(_limb().ROLE_KEY) == 'MASTER':
        return bone.name
    return None


def allowed_parent(armature, bone_name):
    record = get_record(armature)
    return record['master'] if record and bone_name in record['controls'] else None


def extra_constraints(armature):
    record = get_record(armature)
    return {(entry['owner'], entry['name']) for entry in record['constraints']} if record else set()


def owned_constraint_paths(armature):
    return {armature.pose.bones[owner].constraints[name].path_from_id()
            for owner, name in extra_constraints(armature)}


def owned_driver_paths(armature):
    record = get_record(armature)
    return {armature.pose.bones[record['master']].path_from_id('scale')} if record else set()


def collection_members(armature):
    record = get_record(armature)
    names = {record['master']} if record else set()
    return {'generated': names, 'always': names, 'visible': names, 'replaced': set()}


def _scale_path(pb):
    return pb.path_from_id() + '["' + SCALE_PROPERTY + '"]'


def _scale_drivers(armature, pb):
    for index in range(3):
        curve = pb.driver_add('scale', index)
        curve.driver.type = 'SCRIPTED'
        curve.driver.expression = 'max(root_scale,0.001)'
        variable = curve.driver.variables.new()
        variable.name, variable.type = 'root_scale', 'SINGLE_PROP'
        variable.targets[0].id = armature
        variable.targets[0].data_path = _scale_path(pb)


def _validate_scale_drivers(armature, pb):
    value = pb.get(SCALE_PROPERTY)
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error('Root uniform scale must be a finite number.')
    curves = list(armature.animation_data.drivers) if armature.animation_data else []
    own = [c for c in curves if c.data_path == pb.path_from_id('scale')]
    if len(own) != 3 or {c.array_index for c in own} != {0, 1, 2}:
        raise _error('Root uniform-scale drivers were removed or duplicated.')
    for curve in own:
        driver = curve.driver
        if (curve.mute or driver.type != 'SCRIPTED' or driver.expression != 'max(root_scale,0.001)'
                or len(driver.variables) != 1):
            raise _error('Root uniform-scale drivers were edited.')
        variable = driver.variables[0]
        target = variable.targets[0]
        if (variable.name != 'root_scale' or variable.type != 'SINGLE_PROP'
                or target.id != armature or target.data_path != _scale_path(pb)):
            raise _error('Root uniform-scale driver input was edited.')
    if any(c.data_path == _scale_path(pb) for c in curves):
        raise _error('Another driver controls Root uniform scale; preserve that setup first.')


def validate(armature, inventory=None):
    """Validate this extension without entering the limb inventory recursively."""
    record = get_record(armature)
    actual = {b.name for b in armature.data.bones if b.get(OWNER_KEY) == OWNER_VALUE}
    if record is None:
        if actual:
            raise _error('Root Control has lost its recovery data.')
        return None
    try:
        if actual != {record['master']}:
            raise _error('Root Control bones were added, removed, or renamed.')
        master = armature.data.bones[record['master']]
        pb = armature.pose.bones[master.name]
        if (not _owned(master, record, 'MASTER') or not _same_rest(master, record['bone_states']['MASTER'])
                or master.use_deform or master.parent or pb.constraints):
            raise _error('Root Control structure or constraints were edited.')
        if tuple(pb.lock_scale) != (True, True, True):
            raise _error('Use Root uniform scale; its individual scale channels must stay locked.')
        _validate_scale_drivers(armature, pb)
        for name, state in record['source_states'].items():
            bone = armature.data.bones.get(name)
            if bone is None or not _same_rest(bone, state) or bone.use_deform != state['deform']:
                raise _error(f"Root source '{name}' was structurally edited.")
        for name, original in record['control_states'].items():
            bone = armature.data.bones.get(name)
            state = dict(original, parent=record['master'])
            if bone is None or not _same_rest(bone, state) or bone.use_deform != state['deform']:
                raise _error(f"Root input '{name}' was structurally edited.")
        if inventory is not None:
            expected = {data[role].name for data in inventory['rigs'].values() for role in ('target', 'pole')}
            if expected != set(record['controls']):
                raise _error('The limb inventory changed while Root Control was attached.')
        owned = extra_constraints(armature)
        for name in record['sources']:
            if any((name, c.name) not in owned for c in armature.pose.bones[name].constraints):
                raise _error(f"Root source '{name}' has additional constraints; preserve that setup first.")
        for entry in record['constraints']:
            con = armature.pose.bones[entry['owner']].constraints.get(entry['name'])
            if (con is None or con.type != entry['type'] or con.target != armature or con.mute
                    or any(not _same_value(getattr(con, key), value) for key, value in entry['fields'].items())):
                raise _error('A Root follow constraint was edited.')
        collection = bpy.data.collections.get(record['widget_collection'])
        entry = record['widgets']['MASTER']
        obj = bpy.data.objects.get(entry['object'])
        if (not _owned(collection, record, 'WIDGET_COLLECTION') or not _owned(obj, record, 'WIDGET')
                or obj.type != 'MESH' or obj.data.name != entry['mesh']
                or not _owned(obj.data, record, 'WIDGET_MESH') or pb.custom_shape != obj):
            raise _error('Root Control widget was replaced or removed.')
    except (KeyError, TypeError, AttributeError) as exc:
        raise _error('Root Control recovery data no longer matches this rig.') from exc
    return record


def _verify_pose(armature, desired):
    for name, wanted in desired.items():
        actual = armature.pose.bones[name].matrix
        if max(abs(actual[i][j] - wanted[i][j]) for i in range(4) for j in range(4)) > 4e-4:
            raise _error(f"Root Control could not preserve '{name}' in this pose; no changes kept.")


def _add_widget(context, armature, record, *, snapshot=None):
    collection = bpy.data.collections.new(record['widget_collection'])
    context.scene.collection.children.link(collection)
    _tag(collection, record, 'WIDGET_COLLECTION')
    vertices, edges = (_limb()._widget_geometry('MASTER') if snapshot is None
                       else (snapshot['vertices'], snapshot['edges']))
    entry = record['widgets']['MASTER']
    mesh = bpy.data.meshes.new(entry['mesh'])
    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(entry['object'], mesh)
    collection.objects.link(obj)
    _tag(obj, record, 'WIDGET')
    _tag(mesh, record, 'WIDGET_MESH')
    obj.hide_render = obj.hide_select = True
    obj.hide_set(True)
    pb = armature.pose.bones[record['master']]
    pb.custom_shape, pb.use_custom_shape_bone_size = obj, False
    pb.custom_shape_scale_xyz = (record['widget_size'],) * 3
    pb.custom_shape_rotation_euler = (math.pi * .5, 0, 0)
    pb.custom_shape_translation = _limb()._master_widget_translation(armature)
    if hasattr(pb, 'custom_shape_wire_width'):
        pb.custom_shape_wire_width = 2.0
    if snapshot:
        for key, value in snapshot['visual'].items():
            setattr(pb, key, value)
        pb.custom_shape_transform = armature.pose.bones.get(snapshot['transform'] or '')
        from . import control_colors
        control_colors.restore_bone_state(pb, snapshot['color'])


def _add_follow(armature, record):
    for entry in record['constraints']:
        con = armature.pose.bones[entry['owner']].constraints.new(entry['type'])
        con.name, con.target = entry['name'], armature
        for key, value in entry['fields'].items():
            setattr(con, key, value)


def _create_graph(context, armature, record, *, snapshot=None):
    _limb()._mode_set(context, armature, 'EDIT')
    master = armature.data.edit_bones.new(record['master'])
    if master.name != record['master']:
        raise _error('The Root bone name is occupied.')
    _tag(master, record, 'MASTER')
    state = record['bone_states']['MASTER']
    master.head, master.tail = state['head'], state['tail']
    master.align_roll(Matrix(state['matrix']).to_3x3().col[2])
    master.use_deform = False
    for name in record['controls']:
        armature.data.edit_bones[name].parent = master
    _limb()._mode_set(context, armature, 'OBJECT')
    pb = armature.pose.bones[record['master']]
    _tag(pb.bone, record, 'MASTER')
    pb.rotation_mode = 'XYZ'
    pb.matrix_basis = Matrix.Identity(4)
    pb.lock_scale = (True,) * 3
    pb[SCALE_PROPERTY] = 1.0
    pb.id_properties_ui(SCALE_PROPERTY).update(min=.001, soft_min=.05, soft_max=3.0,
        description='Uniformly scale the whole character; individual axis scaling is disabled.')
    _scale_drivers(armature, pb)
    _add_follow(armature, record)
    _add_widget(context, armature, record, snapshot=snapshot)
    armature.data.collections_all[record['control_collection']].assign(pb.bone)


def _delete_graph(context, armature, record):
    for entry in record['constraints']:
        pb = armature.pose.bones.get(entry['owner'])
        con = pb.constraints.get(entry['name']) if pb else None
        if con:
            pb.constraints.remove(con)
    pb = armature.pose.bones.get(record['master'])
    if pb:
        for index in range(3):
            pb.driver_remove('scale', index)
    _limb()._mode_set(context, armature, 'EDIT')
    for name, state in record['control_states'].items():
        bone = armature.data.edit_bones.get(name)
        if bone:
            bone.parent = armature.data.edit_bones.get(state['parent']) if state['parent'] else None
    bone = armature.data.edit_bones.get(record['master'])
    if bone and bone.get(OWNER_KEY) == OWNER_VALUE:
        armature.data.edit_bones.remove(bone)
    _limb()._mode_set(context, armature, 'OBJECT')
    for entry in record['widgets'].values():
        obj = bpy.data.objects.get(entry['object'])
        if obj and obj.get(ID_KEY) == record['id']:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if not mesh.users:
                bpy.data.meshes.remove(mesh)
    collection = bpy.data.collections.get(record['widget_collection'])
    if collection and collection.get(ID_KEY) == record['id'] and not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)


def build(context, armature):
    """Add a neutral global root; an existing enhanced Master is reused unchanged."""
    from . import bone_collections
    _active(context, armature)
    previous = validate(armature)
    if previous:
        return previous
    inventory = _limb()._validate_inventory(armature)
    if inventory.get('master'):
        return {'master': inventory['master'].name, 'reused': True}
    if not inventory['rigs'] or not _limb()._is_direct_preroll_schema(inventory['schema']):
        raise _error('Build current Direct limb controls before adding Root Control.')
    if ROOT_NAME in armature.data.bones:
        raise _error('The Root Control bone name is occupied; keep that existing bone intact.')
    controls = sorted({data[role].name for data in inventory['rigs'].values() for role in ('target', 'pole')})
    if any(armature.data.bones[name].parent is not None for name in controls):
        raise _error('A limb input already has a parent; preserve that rig before adding Root Control.')
    sources = sorted(b.name for b in armature.data.bones if b.parent is None
                     and b.get(OWNER_KEY) not in _limb().GENERATED_CONTROL_OWNERS)
    if not sources or any(armature.pose.bones[name].constraints for name in sources):
        raise _error('Root Control needs native roots without other constraints.')
    groups = [c for c in armature.data.collections_all if c.get(_limb().OWNER_KEY) == _limb().OWNER_VALUE
              and c.get(_limb().ROLE_KEY) == 'CONTROL_COLLECTION']
    if len(groups) != 1:
        raise _error('The generated Controls collection is missing or duplicated.')
    _update(context, armature)
    desired = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    bases = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in armature.pose.bones}
    size = _limb()._master_bone_size(armature)
    identity = [list(row) for row in Matrix.Identity(4)]
    record = {'version': VERSION, 'id': uuid.uuid4().hex, 'master': ROOT_NAME,
              'bones': {'MASTER': ROOT_NAME}, 'controls': controls, 'sources': sources,
              'source_states': {name: _state(armature.data.bones[name]) for name in sources},
              'control_states': {name: _state(armature.data.bones[name]) for name in controls},
              'bone_states': {'MASTER': {'head': [0., 0., 0.], 'tail': [0., size, 0.],
                  'matrix': identity, 'parent': '', 'deform': False, 'connect': False,
                  'inherit_scale': 'FULL', 'inherit_rotation': True, 'local_location': True}},
              'control_collection': groups[0].name, 'constraints': [], 'widget_size': size * 2.8}
    record['widget_collection'] = 'CD_Root_Widgets_' + record['id'][:10]
    name = 'WGT_CD_Root_' + record['id'][:10]
    record['widgets'] = {'MASTER': {'object': name, 'mesh': name}}
    for source in sources:
        record['constraints'].append({'owner': source, 'name': 'CD Root Follow', 'type': 'COPY_TRANSFORMS',
            'fields': {'subtarget': ROOT_NAME, 'owner_space': 'POSE', 'target_space': 'POSE',
                       'mix_mode': 'BEFORE', 'influence': 1.0}})
    layout = bone_collections.capture_managed_layout(armature)
    ctx = _limb()._capture_context(context, armature)
    mirror = armature.data.use_mirror_x
    try:
        armature.data.use_mirror_x = False
        _create_graph(context, armature, record)
        armature.data[RECORD_KEY] = json.dumps(record)
        _update(context, armature)
        validate(armature)
        _verify_pose(armature, desired)
        bone_collections.finish_rig_edit(armature, layout)
        _update(context, armature)
        _verify_pose(armature, desired)
    except Exception:
        _delete_graph(context, armature, record)
        armature.data.pop(RECORD_KEY, None)
        for name, (rotation_mode, basis) in bases.items():
            pb = armature.pose.bones[name]
            pb.rotation_mode, pb.matrix_basis = rotation_mode, basis
        bone_collections.restore_layout(armature, layout)
        _update(context, armature)
        raise
    finally:
        armature.data.use_mirror_x = mirror
        _limb()._restore_context(context, armature, ctx)
    return record


def _refuse_dependencies(armature, record):
    from .foot_controls import _driver_owners
    names = {record['master']}
    changed = names | set(record['sources']) | set(record['controls'])
    paths = owned_driver_paths(armature)
    curves = [c for action in _limb()._actions_for_id(armature) for c in _limb()._fcurves_for_action(action)]
    if armature.animation_data:
        curves.extend(c for c in armature.animation_data.drivers if c.data_path not in paths)
    if any(_limb()._path_mentions_bone(c.data_path, changed) for c in curves):
        raise _error('Root or its inputs have animation or drivers; preserve those channels before removal.')
    for bone in armature.data.bones:
        if bone.parent and bone.parent.name in names and bone.name not in record['controls']:
            raise _error('Another bone follows Root Control; detach it before removal.')
    owned = extra_constraints(armature)
    widget = record['widgets']['MASTER']['object']
    for obj in bpy.data.objects:
        if obj.parent == armature and obj.parent_type == 'BONE' and obj.parent_bone in names:
            raise _error('An object follows Root Control; detach it before removal.')
        for con in obj.constraints:
            if _limb()._constraint_references_controls(con, armature, names):
                raise _error('An object constraint follows Root Control; detach it before removal.')
        if obj.type != 'ARMATURE':
            continue
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if obj == armature and (pb.name, con.name) in owned:
                    continue
                if _limb()._constraint_references_controls(con, armature, names):
                    raise _error('Another constraint follows Root Control; detach it before removal.')
            if pb.custom_shape and pb.custom_shape.name == widget and (obj != armature or pb.name not in names):
                raise _error('The Root widget is shared; make that use independent first.')
            if obj == armature and pb.name not in names and pb.custom_shape_transform and pb.custom_shape_transform.name in names:
                raise _error('Another widget follows Root Control; detach it before removal.')
    for owner in _driver_owners():
        animation = getattr(owner, 'animation_data', None)
        for curve in animation.drivers if animation else ():
            if owner == armature and curve.data_path in paths:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    reads_transform = (_limb()._path_mentions_bone(target.data_path, changed)
                                       and any('.' + channel in target.data_path for channel in
                                               ('location', 'rotation_', 'scale', 'matrix')))
                    if target.id == armature and (target.bone_target in changed or reads_transform
                                                  or _limb()._path_mentions_bone(target.data_path, names)):
                        raise _error('Another driver reads Root Control; preserve that setup first.')
    collection = bpy.data.collections[record['widget_collection']]
    obj = bpy.data.objects[widget]
    if (collection.children or collection.users > 1 or set(collection.objects.keys()) != {widget}
            or obj.data.users != 1 or tuple(obj.users_collection) != (collection,) or obj.users > 2):
        raise _error('The Root widget contains or shares artist data; separate that use before removal.')


def _widget_snapshot(armature, record):
    from . import control_colors
    pb = armature.pose.bones[record['master']]
    return {'vertices': [list(v.co) for v in pb.custom_shape.data.vertices],
            'edges': [list(e.vertices) for e in pb.custom_shape.data.edges],
            'transform': pb.custom_shape_transform.name if pb.custom_shape_transform else None,
            'color': control_colors.capture_bone(pb),
            'visual': {key: (list(getattr(pb, key)) if key != 'use_custom_shape_bone_size' else getattr(pb, key))
                       for key in ('custom_shape_translation', 'custom_shape_rotation_euler',
                                   'custom_shape_scale_xyz', 'use_custom_shape_bone_size')}}


def _capture_match_inventory(inventory):
    """Retain names rather than invalidatable Bone/Constraint RNA across Edit Mode."""
    roles = ('target', 'pole', 'solver_target', 'ori_upper', 'ori_lower')
    rigs = {}
    for key, rig in inventory['rigs'].items():
        saved = dict(rig)
        for role in roles:
            saved[role] = rig[role].name if rig.get(role) else None
        saved['entries'] = [(pb.name, con.name, record) for pb, con, record in rig['entries']]
        offset = rig.get('auto_offset_rotation')
        saved['auto_offset_rotation'] = offset.name if offset else None
        rigs[key] = saved
    return {'schema': inventory['schema'], 'rigs': rigs}


def _resolve_match_inventory(armature, snapshot):
    result = {'schema': snapshot['schema'], 'rigs': {}}
    for key, saved in snapshot['rigs'].items():
        rig = dict(saved)
        for role in ('target', 'pole', 'solver_target', 'ori_upper', 'ori_lower'):
            rig[role] = armature.data.bones[saved[role]] if saved.get(role) else None
        rig['entries'] = [(armature.pose.bones[owner], armature.pose.bones[owner].constraints[name], record)
                          for owner, name, record in saved['entries']]
        rig['auto_offset_rotation'] = (armature.pose.bones[rig['chain'][2]].constraints[saved['auto_offset_rotation']]
                                       if saved['auto_offset_rotation'] else None)
        result['rigs'][key] = rig
    return result


def _restore_end_offsets(context, armature, inventory, desired, modes):
    """Unparenting changes Local target rotation, while endpoints already match.

    Preserve the existing pole planes exactly: a general FK-to-IK match would
    unnecessarily re-plan those inputs after the global transform was baked.
    """
    for key, rig in inventory['rigs'].items():
        if modes[key] != 'IK' or rig.get('foot_controls'):
            continue
        target = armature.pose.bones[rig['target'].name]
        end = armature.pose.bones[rig['chain'][2]]
        wanted = desired[end.name]
        offset = rig.get('auto_offset_rotation')
        con = next(con for _pb, con, entry in rig['entries'] if entry['role'] == 'END_ROTATION')
        if rig['auto_align'] and offset is not None:
            old_mute = offset.mute
            offset.mute = True
            try:
                _update(context, armature)
                natural = armature.convert_space(pose_bone=end, matrix=end.matrix.copy(), from_space='POSE', to_space='LOCAL')
                wanted_local = armature.convert_space(pose_bone=end, matrix=wanted, from_space='POSE', to_space='LOCAL')
                rotation = natural.to_quaternion().inverted() @ wanted_local.to_quaternion()
                basis = target.matrix_basis.copy()
                target.matrix_basis = Matrix.LocRotScale(basis.translation, rotation.normalized(), basis.to_scale())
            finally:
                offset.mute = old_mute
            _update(context, armature)
        elif con.target_space == 'LOCAL_OWNER_ORIENT':
            wanted_local = armature.convert_space(pose_bone=end, matrix=wanted, from_space='POSE', to_space='LOCAL')
            basis = target.matrix_basis.copy()
            target.matrix_basis = Matrix.LocRotScale(basis.translation, wanted_local.to_quaternion(), basis.to_scale())
            _update(context, armature)
            wanted_world = armature.matrix_world @ wanted
            for _iteration in range(8):
                current = armature.matrix_world @ end.matrix
                if _limb()._rotation_error(current, wanted_world) < 3e-4:
                    break
                correction = wanted_world.to_quaternion().normalized() @ current.to_quaternion().normalized().inverted()
                current_target = armature.matrix_world @ target.matrix
                corrected = Matrix.LocRotScale(current_target.translation,
                    correction @ current_target.to_quaternion().normalized(), current_target.to_scale())
                target.matrix = armature.matrix_world.inverted_safe() @ corrected
                _update(context, armature)


def remove(context, armature):
    """Bake this root into existing controls without changing native rest data or keys."""
    from . import bone_collections, limb_ik_fk
    _active(context, armature)
    record = validate(armature)
    if record is None:
        if control_name(armature):
            raise _error('This Master belongs to the enhanced limb rig; manage it with that rig.')
        raise _error('This character has no optional Root Control to remove.')
    _refuse_dependencies(armature, record)
    inventory = _limb()._validate_inventory(armature)
    match_snapshot = _capture_match_inventory(inventory)
    modes = {key: limb_ik_fk.mode_for_rig(armature, rig) for key, rig in inventory['rigs'].items()}
    if 'BLEND' in modes.values():
        raise _error('Switch each limb to IK or FK before removing Root Control.')
    _update(context, armature)
    desired = {pb.name: pb.matrix.copy() for pb in armature.pose.bones if pb.name != record['master']}
    # Auto hand input coordinates must change after removing their parent; their
    # visible widget follows the preserved native hand. Validate all other bones.
    verify = {name: matrix for name, matrix in desired.items() if name not in record['controls']}
    bases = {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in armature.pose.bones}
    root_scale = armature.pose.bones[record['master']][SCALE_PROPERTY]
    mode_props = {rig['target'].name: armature.pose.bones[rig['target'].name].get(limb_ik_fk.PROPERTY)
                  for rig in inventory['rigs'].values()}
    widget = _widget_snapshot(armature, record)
    layout = bone_collections.capture_managed_layout(armature)
    ctx = _limb()._capture_context(context, armature)
    mirror = armature.data.use_mirror_x
    deleted = False
    try:
        armature.data.use_mirror_x = False
        for entry in record['constraints']:
            armature.pose.bones[entry['owner']].constraints[entry['name']].mute = True
        _limb()._mode_set(context, armature, 'EDIT')
        for name, state in record['control_states'].items():
            armature.data.edit_bones[name].parent = armature.data.edit_bones.get(state['parent']) if state['parent'] else None
        _limb()._mode_set(context, armature, 'OBJECT')
        inventory = _resolve_match_inventory(armature, match_snapshot)
        for name in (*record['sources'], *record['controls']):
            armature.pose.bones[name].matrix = desired[name]
        _update(context, armature)
        _restore_end_offsets(context, armature, inventory, desired, modes)
        _update(context, armature)
        _verify_pose(armature, verify)
        _delete_graph(context, armature, record)
        deleted = True
        del armature.data[RECORD_KEY]
        bone_collections.finish_rig_edit(armature, layout)
        _update(context, armature)
        _verify_pose(armature, verify)
    except Exception:
        if deleted:
            _create_graph(context, armature, record, snapshot=widget)
        else:
            _limb()._mode_set(context, armature, 'EDIT')
            for name in record['controls']:
                armature.data.edit_bones[name].parent = armature.data.edit_bones[record['master']]
            _limb()._mode_set(context, armature, 'OBJECT')
            for entry in record['constraints']:
                armature.pose.bones[entry['owner']].constraints[entry['name']].mute = False
        armature.data[RECORD_KEY] = json.dumps(record)
        armature.pose.bones[record['master']][SCALE_PROPERTY] = root_scale
        for name, (rotation_mode, matrix) in bases.items():
            pb = armature.pose.bones[name]
            pb.rotation_mode, pb.matrix_basis = rotation_mode, matrix
        for name, value in mode_props.items():
            if value is None:
                armature.pose.bones[name].pop(limb_ik_fk.PROPERTY, None)
            else:
                armature.pose.bones[name][limb_ik_fk.PROPERTY] = value
        bone_collections.restore_layout(armature, layout)
        _update(context, armature)
        raise
    finally:
        armature.data.use_mirror_x = mirror
        _limb()._restore_context(context, armature, ctx)
    return {'bones_removed': 1, 'pose_preserved': True}
