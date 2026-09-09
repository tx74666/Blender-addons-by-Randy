"""Optional, removable foot-roll and toe controls on an existing Limb IK rig."""
from __future__ import annotations

import json
import math
import uuid

import bpy
from mathutils import Matrix, Vector

OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'foot_controls'
ROLE_KEY = 'character_designer_foot_role'
SIDE_KEY = 'character_designer_foot_side'
ID_KEY = 'character_designer_foot_id'
RECORD_KEY = 'character_designer_foot_controls_v1'
VERSION = 1
_SUSPENDED = set()


def _limb():
    from . import limb_ik
    return limb_ik


def _error(message):
    return _limb().LimbIKError(message)


def _side(key):
    side = key[1] if isinstance(key, (tuple, list)) and len(key) == 2 and key[0] == 'LEG' else key
    if side not in {'L', 'R'}:
        raise _error('Choose the left or right leg for Foot Controls.')
    return side


def _all_records(armature):
    raw = armature.data.get(RECORD_KEY)
    if raw is None:
        return {}
    try:
        payload = json.loads(raw)
        if payload['version'] != VERSION or not isinstance(payload['legs'], dict):
            raise ValueError('unsupported version')
        return payload['legs']
    except (KeyError, TypeError, ValueError) as exc:
        raise _error('Foot Controls recovery data is invalid; restore a saved copy.') from exc


def records(armature):
    return {side: record for side, record in _all_records(armature).items()
            if (armature.as_pointer(), side) not in _SUSPENDED}


def _write_records(armature, values):
    if values:
        armature.data[RECORD_KEY] = json.dumps({'version': VERSION, 'legs': values}, separators=(',', ':'), sort_keys=True)
    else:
        armature.data.pop(RECORD_KEY, None)


def get_record(armature, key, *, rig_id=None):
    if isinstance(key, (tuple, list)) and key[0] != 'LEG':
        return None
    record = records(armature).get(_side(key))
    if record is not None and rig_id is not None and record.get('rig_id') != rig_id:
        raise _error('Foot Controls belongs to a different generated leg; restore its original rig.')
    return record


def collection_members(armature):
    result = {'generated': set(), 'replaced': set(), 'always': set(), 'ik': {}, 'hidden_base': set()}
    for record in records(armature).values():
        result['generated'].update(record['bones'].values())
        result['replaced'].add(record['toe'])
        result['always'].add(record['toe_control'])
        result['ik'].setdefault(record['target'], set()).add(record['roll'])
        if record.get('legacy_heel'):
            result['hidden_base'].add(record['legacy_heel'])
    return result


def validate(armature, inventory=None):
    """Validate the optional extension, without recursing into Limb IK inventory."""
    values = records(armature)
    expected = set()
    for side, record in values.items():
        if side not in {'L', 'R'} or record.get('version') != VERSION or record.get('side') != side:
            raise _error('Foot Controls record is incomplete.')
        if inventory is not None:
            base = inventory['rigs'].get(('LEG', side))
            if base is None or base['rig_id'] != record['rig_id']:
                raise _error('Foot Controls no longer matches its original leg.')
        for role, name in record['bones'].items():
            bone = armature.data.bones.get(name)
            state = record['bone_states'][role]
            if (bone is None or bone.get(OWNER_KEY) != OWNER_VALUE
                    or bone.get(ID_KEY) != record['id'] or bone.get(ROLE_KEY) != role
                    or bone.get(SIDE_KEY) != side or bone.use_deform
                    or (bone.parent.name if bone.parent else None) != state['parent']
                    or (bone.head_local - Vector(state['head'])).length > 1e-5
                    or (bone.tail_local - Vector(state['tail'])).length > 1e-5
                    or _limb()._rotation_error(bone.matrix_local, Matrix(state['matrix'])) > 1e-3):
                raise _error(f"Foot Controls bone '{name}' was removed or its structure was edited.")
            expected.add(name)
        owned_cons = {(entry['owner'], entry['name']) for entry in record['constraints']}
        for name in record['bones'].values():
            for con in armature.pose.bones[name].constraints:
                if (name, con.name) not in owned_cons:
                    raise _error(f"Another constraint was added to Foot Controls bone '{name}'; keep it or detach it before changing the setup.")
        for entry in record['constraints']:
            pb = armature.pose.bones.get(entry['owner'])
            con = pb.constraints.get(entry['name']) if pb else None
            if con is None or con.type != entry['type']:
                raise _error(f"Foot Controls constraint '{entry['name']}' is missing.")
            if con.mute:
                raise _error(f"Foot Controls constraint '{con.name}' was disabled.")
            if con.name != 'CD Foot IK Toe Space' and abs(con.influence - 1.0) > 1e-6:
                raise _error(f"Foot Controls constraint '{con.name}' influence was edited.")
            for name, value in entry['fields'].items():
                actual = getattr(con, name)
                if actual != value:
                    raise _error(f"Foot Controls constraint '{con.name}' was edited.")
            if con.target is not armature:
                raise _error('Foot Controls must follow its original armature.')
        for entry in record['drivers']:
            _validate_driver(armature, entry)
        if armature.animation_data:
            owned_drivers = {(entry['path'], entry['index']) for entry in record['drivers']}
            for curve in armature.animation_data.drivers:
                if (_limb()._path_mentions_bone(curve.data_path, set(record['bones'].values()))
                        and (curve.data_path, curve.array_index) not in owned_drivers):
                    raise _error('Another driver writes to Foot Controls; keep that dependency intact.')
        collection = bpy.data.collections.get(record['widget_collection'])
        if collection is None or collection.get(ID_KEY) != record['id']:
            raise _error('Foot Controls widget collection is missing.')
        for role, entry in record['widgets'].items():
            obj = bpy.data.objects.get(entry['object'])
            pb = armature.pose.bones[record['bones'][role]]
            if (obj is None or obj.get(ID_KEY) != record['id'] or obj.type != 'MESH'
                    or obj.data.name != entry['mesh'] or pb.custom_shape is not obj):
                raise _error('Foot Controls custom shape was replaced or removed.')
    actual = {bone.name for bone in armature.data.bones if bone.get(OWNER_KEY) == OWNER_VALUE
              and (armature.as_pointer(), bone.get(SIDE_KEY)) not in _SUSPENDED}
    if actual != expected:
        raise _error('Foot Controls contains unregistered generated bones.')
    return values


def owned_bone_names(armature):
    return {name for record in records(armature).values() for name in record['bones'].values()}


def owned_constraint_paths(armature):
    return {armature.pose.bones[e['owner']].constraints[e['name']].path_from_id()
            for record in records(armature).values() for e in record['constraints']}


def owned_driver_paths(armature):
    return {entry['path'] for record in records(armature).values() for entry in record['drivers']}


def _tag(item, record, role):
    item[OWNER_KEY] = OWNER_VALUE
    item[ID_KEY] = record['id']
    item[ROLE_KEY] = role
    item[SIDE_KEY] = record['side']


def _update(context, armature):
    armature.update_tag(refresh={'OBJECT'})
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()


def _active(context, armature):
    if (armature is None or armature.type != 'ARMATURE' or context.object is not armature
            or armature.mode not in {'OBJECT', 'POSE'} or armature.library or armature.data.library
            or not armature.is_editable or armature.data.users != 1):
        raise _error('Select a local, single-user armature in Object or Pose Mode.')


def _matrix(value):
    return [list(row) for row in value]


def _frame(position, rotation):
    result = rotation.to_4x4()
    result.translation = position
    return result


def _curve_mentions(armature, names, *, include_drivers=True):
    curves = [curve for action in _limb()._actions_for_id(armature)
              for curve in _limb()._fcurves_for_action(action)]
    if include_drivers and armature.animation_data:
        curves += list(armature.animation_data.drivers)
    return any(_limb()._path_mentions_bone(curve.data_path, names) for curve in curves)


_VISUAL_FIELDS = ('custom_shape_translation', 'custom_shape_rotation_euler',
                  'custom_shape_scale_xyz', 'use_custom_shape_bone_size')


def _roll_visual_state(pose_bone):
    return {'transform': pose_bone.custom_shape_transform.name if pose_bone.custom_shape_transform else None,
            **{name: list(getattr(pose_bone, name)) for name in _VISUAL_FIELDS[:3]},
            'use_custom_shape_bone_size': pose_bone.use_custom_shape_bone_size}


def _set_roll_visual(armature, pose_bone, state):
    transform = armature.pose.bones.get(state['transform']) if state['transform'] else None
    if state['transform'] and transform is None:
        raise _error('The saved Foot Roll display bone is missing; restore that bone before restoring its display.')
    pose_bone.custom_shape_transform = transform
    for name in _VISUAL_FIELDS:
        setattr(pose_bone, name, state[name])


def _visual_animation_guard(armature, pose_bone):
    paths = {pose_bone.path_from_id(name) for name in (*_VISUAL_FIELDS, 'custom_shape_transform')}
    curves = [curve for action in _limb()._actions_for_id(armature)
              for curve in _limb()._fcurves_for_action(action)]
    if armature.animation_data:
        curves += list(armature.animation_data.drivers)
    if any(curve.data_path in paths for curve in curves):
        raise _error('Foot Roll display offsets have animation or a driver; keep that display animation before fitting.')


def _foot_display_frame(armature, foot_name, toe_name):
    """Anatomical ground axes in the native foot's local space, independent of pose."""
    foot, toe = armature.data.bones[foot_name], armature.data.bones[toe_name]
    up = Vector((0, 0, 1))
    forward = toe.tail_local - foot.head_local
    forward -= up * forward.dot(up)
    if forward.length < 1e-5:
        raise _error('The rest foot needs a readable forward direction to fit Foot Roll.')
    forward.normalize()
    right = forward.cross(up).normalized()
    up = right.cross(forward).normalized()
    ground = Matrix((right, forward, up)).transposed()
    rotation = foot.matrix_local.to_quaternion().to_matrix().transposed() @ ground
    to_ground = ground.transposed()
    ball = to_ground @ (toe.head_local - foot.head_local)
    tip = to_ground @ (toe.tail_local - foot.head_local)
    return rotation, ball, tip


def _distance_to_segment(point, start, end):
    delta = end - start
    factor = min(max((point - start).dot(delta) / max(delta.length_squared, 1e-12), 0.0), 1.0)
    return (point - start - delta * factor).length_squared


def _shoe_points(context, armature, foot_name, toe_name, shoe, rotation):
    """Read evaluated shoe geometry and separate paired shoes by weights or proximity."""
    if (not isinstance(shoe, bpy.types.Object) or shoe.type != 'MESH'
            or context.scene.objects.get(shoe.name) != shoe or shoe.get(OWNER_KEY) == OWNER_VALUE):
        raise _error('Choose a shoe mesh in this scene as the Foot Roll fit reference.')
    modifiers = [mod for mod in shoe.modifiers if mod.type == 'ARMATURE' and mod.show_viewport and mod.object]
    if any(mod.object != armature for mod in modifiers):
        raise _error('The shoe reference follows a different armature; choose this character\'s shoe mesh.')
    posed = bool(modifiers) or (shoe.parent == armature and shoe.parent_type == 'BONE')
    if shoe.parent_type == 'BONE' and shoe.parent and shoe.parent != armature:
        raise _error('The shoe reference is attached to a different character.')
    bone_matrix = lambda name: armature.pose.bones[name].matrix if posed else armature.data.bones[name].matrix_local
    source_world = armature.matrix_world @ bone_matrix(foot_name)
    candidates = {foot_name: toe_name}
    for bone in armature.data.bones:
        if (bone.use_deform and bone.parent and bone.parent.use_deform
                and 'toe' in bone.name.lower()):
            candidates.setdefault(bone.parent.name, bone.name)
    group_names = {}
    segments = {}
    for name, child_name in candidates.items():
        parent = armature.data.bones[name].parent
        group_names[name] = {name, child_name} | ({parent.name} if parent else set())
        toe_bone = armature.data.bones[child_name]
        segments[name] = (armature.matrix_world @ bone_matrix(name).translation,
                          armature.matrix_world @ bone_matrix(child_name).translation,
                          armature.matrix_world @ (bone_matrix(child_name) @ Vector((0, toe_bone.length, 0))))
    group_indices = {name: {group.index for group in shoe.vertex_groups if group.name in names}
                     for name, names in group_names.items()}
    evaluated = shoe.evaluated_get(context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=context.evaluated_depsgraph_get())
    points = []
    try:
        if mesh is None:
            raise _error('The shoe reference has no evaluated mesh.')
        to_local = source_world.inverted_safe()
        to_ground = rotation.transposed()
        for vertex in mesh.vertices:
            world = evaluated.matrix_world @ vertex.co
            weights = {name: sum(item.weight for item in vertex.groups if item.group in indices)
                       for name, indices in group_indices.items()}
            strongest = max(weights.values(), default=0.0)
            if strongest > 1e-5:
                if weights[foot_name] < strongest or weights[foot_name] <= 1e-5:
                    continue
            else:
                distances = {name: min(_distance_to_segment(world, ankle, ball),
                                       _distance_to_segment(world, ball, tip))
                             for name, (ankle, ball, tip) in segments.items()}
                if distances[foot_name] > min(distances.values()) + 1e-10:
                    continue
            point = to_ground @ (to_local @ world)
            if not all(math.isfinite(value) for value in point):
                raise _error('The shoe reference contains invalid coordinates.')
            points.append(point)
    finally:
        evaluated.to_mesh_clear()
    if len(points) < 3:
        raise _error('The shoe reference has no usable geometry for this foot; choose its shoe mesh.')
    return points


def _roll_visual_plan(context, armature, foot_name, toe_name, shoe=None):
    rotation, ball, tip = _foot_display_frame(armature, foot_name, toe_name)
    length = max(tip.y, (tip - ball).length + ball.length, 0.01)
    if shoe is not None:
        points = _shoe_points(context, armature, foot_name, toe_name, shoe, rotation)
        minimum = Vector(tuple(min(point[i] for point in points) for i in range(3)))
        maximum = Vector(tuple(max(point[i] for point in points) for i in range(3)))
        length = max(maximum.y - minimum.y, length)
        heel_band = [point.x for point in points if point.y <= minimum.y + length * 0.2]
        center_x = (min(heel_band) + max(heel_band)) * 0.5
    else:
        minimum = Vector((-length * 0.16, -ball.length * 0.24, min(ball.z, tip.z)))
        maximum = Vector((length * 0.16, tip.y, 0))
        center_x = 0.0
    size = length * 0.32
    wire, _edges = _limb()._widget_geometry('HEEL')
    # Keep the complete wire behind the heel, with a gap, and above the sole.
    center = Vector((center_x, minimum.y - length * 0.10 - max(v[1] for v in wire) * size,
                     max(0.0, minimum.z + length * 0.10 - min(v[2] for v in wire) * size)))
    state = {'transform': foot_name, 'custom_shape_translation': list(rotation @ center),
             'custom_shape_rotation_euler': list(rotation.to_euler('XYZ')),
             'custom_shape_scale_xyz': [size] * 3, 'use_custom_shape_bone_size': False}
    metadata = {'version': 1, 'source': 'SHOE' if shoe is not None else 'ANATOMY',
                'reference': shoe.name if shoe is not None else None,
                'minimum': list(minimum), 'maximum': list(maximum), 'gap': length * 0.10}
    return state, metadata


def has_roll_visual_backup(armature, key):
    record = get_record(armature, key)
    return bool(record and record.get('roll_visual_backup'))


def fit_roll_visual(context, armature, key, shoe=None):
    """Fit only the visible Foot Roll wire; preserve rig geometry, input channels and animation."""
    _active(context, armature)
    _limb()._validate_inventory(armature)
    record = get_record(armature, key)
    if record is None:
        raise _error('Add Foot Controls before fitting their display.')
    roll = armature.pose.bones[record['roll']]
    _visual_animation_guard(armature, roll)
    _update(context, armature)
    state, metadata = _roll_visual_plan(context, armature, record['chain'][2], record['toe'], shoe)
    previous, values = _roll_visual_state(roll), _all_records(armature)
    if 'roll_visual_backup' not in record:
        record['roll_visual_backup'] = {'state': previous, 'fit': record.get('roll_visual_fit')}
    record['roll_visual_fit'] = metadata
    try:
        _set_roll_visual(armature, roll, state)
        _write_records(armature, dict(values, **{record['side']: record}))
        _update(context, armature)
    except Exception:
        _set_roll_visual(armature, roll, previous)
        _write_records(armature, values)
        raise
    return record


def restore_roll_visual(context, armature, key):
    """Restore the exact display saved before the first explicit Fit, including older layouts."""
    _active(context, armature)
    _limb()._validate_inventory(armature)
    record = get_record(armature, key)
    if record is None or not record.get('roll_visual_backup'):
        raise _error('There is no saved Foot Roll display to restore.')
    roll = armature.pose.bones[record['roll']]
    _visual_animation_guard(armature, roll)
    previous, values = _roll_visual_state(roll), _all_records(armature)
    backup = record.pop('roll_visual_backup')
    if backup.get('fit') is None:
        record.pop('roll_visual_fit', None)
    else:
        record['roll_visual_fit'] = backup['fit']
    try:
        _set_roll_visual(armature, roll, backup['state'])
        _write_records(armature, dict(values, **{record['side']: record}))
        _update(context, armature)
    except Exception:
        _set_roll_visual(armature, roll, previous)
        _write_records(armature, values)
        raise
    return record


def resolve_toe(armature, rig, toe_name=None):
    foot = armature.data.bones[rig['chain'][2]]
    if toe_name:
        toe = armature.data.bones.get(toe_name)
        if toe is None or toe.parent != foot or not toe.use_deform:
            raise _error('Choose the deform toe bone directly parented to this foot.')
        return toe
    candidates = [bone for bone in foot.children if bone.use_deform and 'toe' in bone.name.lower()]
    if len(candidates) != 1:
        raise _error('Choose one Toe Bone; this foot has no unique deform toe child.')
    return candidates[0]


def _constraint_state(owner, constraint):
    fields = {name: getattr(constraint, name) for name in
              ('subtarget', 'target_space', 'owner_space') if hasattr(constraint, name)}
    if hasattr(constraint, 'mix_mode'):
        fields['mix_mode'] = constraint.mix_mode
    return {'owner': owner.name, 'name': constraint.name, 'fields': fields}


def _set_fields(armature, entries):
    for entry in entries:
        con = armature.pose.bones[entry['owner']].constraints[entry['name']]
        for key, value in entry['fields'].items():
            setattr(con, key, value)


def _driver(armature, owner, property_name, index, expression, variables):
    curve = owner.driver_add(property_name, index) if index >= 0 else owner.driver_add(property_name)
    curve.driver.type = 'SCRIPTED'
    curve.driver.expression = expression
    for name, path in variables.items():
        variable = curve.driver.variables.new()
        variable.name = name
        variable.type = 'SINGLE_PROP'
        variable.targets[0].id = armature
        variable.targets[0].data_path = path
    # A new driver includes an identity Generator. Keep a minimal explicit
    # identity mapping so saved evaluation never depends on UI defaults.
    for modifier in tuple(curve.modifiers):
        curve.modifiers.remove(modifier)
    for x in (0.0, 1.0):
        point = curve.keyframe_points.insert(x, x)
        point.interpolation = 'LINEAR'
    curve.extrapolation = 'LINEAR'
    return {'path': curve.data_path, 'index': curve.array_index,
            'expression': expression, 'variables': variables}


def _validate_driver(armature, entry):
    animation = armature.animation_data
    curves = [c for c in animation.drivers if c.data_path == entry['path'] and c.array_index == entry['index']] if animation else []
    if len(curves) != 1:
        raise _error('Foot Controls driver is missing or duplicated.')
    curve = curves[0]
    driver = curve.driver
    if (driver.type != 'SCRIPTED' or driver.expression != entry['expression'] or curve.mute
            or curve.modifiers or len(driver.variables) != len(entry['variables'])):
        raise _error('Foot Controls driver was edited.')
    for variable in driver.variables:
        target = variable.targets[0]
        if (variable.type != 'SINGLE_PROP' or target.id is not armature
                or entry['variables'].get(variable.name) != target.data_path):
            raise _error('Foot Controls driver target was edited.')


def _add_constraint(record, owner, kind, name, armature, **fields):
    con = owner.constraints.new(kind)
    con.name = name
    con.target = armature
    for key, value in fields.items():
        setattr(con, key, value)
    record['constraints'].append({'owner': owner.name, 'name': con.name,
                                  'type': kind, 'fields': fields})
    return con


def _add_widget(context, armature, record, role, kind, scale):
    collection = bpy.data.collections.get(record['widget_collection'])
    if collection is None:
        collection = bpy.data.collections.new(record['widget_collection'])
        context.scene.collection.children.link(collection)
        _tag(collection, record, 'WIDGET_COLLECTION')
    name = 'WGT_CD_' + role + '_' + record['id'][:10]
    vertices, edges = _limb()._widget_geometry(kind)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    _tag(obj, record, 'WIDGET')
    _tag(mesh, record, 'WIDGET_MESH')
    obj.hide_render = True
    obj.hide_set(True)
    obj.hide_select = True
    pb = armature.pose.bones[record['bones'][role]]
    pb.custom_shape = obj
    pb.use_custom_shape_bone_size = False
    pb.custom_shape_scale_xyz = (scale,) * 3
    if hasattr(pb, 'custom_shape_wire_width'):
        pb.custom_shape_wire_width = 2.0
    record['widgets'][role] = {'object': obj.name, 'mesh': mesh.name}


def _verify_pose(armature, desired):
    worst = [0.0, 0.0]
    for name, matrix in desired.items():
        current = armature.pose.bones[name].matrix
        worst[0] = max(worst[0], (current.translation - matrix.translation).length)
        worst[1] = max(worst[1], _limb()._rotation_error(current, matrix))
    if worst[0] > 3e-4 or worst[1] > 3e-3:
        raise _error(f'Foot Controls could not preserve this pose ({worst[0]:.4g}, {worst[1]:.4g}); no changes kept.')
    return worst


def _delete_graph(context, armature, record):
    animation = armature.animation_data
    if animation:
        for entry in record['drivers']:
            for curve in tuple(animation.drivers):
                if curve.data_path == entry['path'] and curve.array_index == entry['index']:
                    animation.drivers.remove(curve)
    for entry in reversed(record['constraints']):
        owner = armature.pose.bones.get(entry['owner'])
        con = owner.constraints.get(entry['name']) if owner else None
        if con:
            owner.constraints.remove(con)
    mirror_before = armature.data.use_mirror_x
    armature.data.use_mirror_x = False
    try:
        _limb()._mode_set(context, armature, 'EDIT')
        for name in reversed(tuple(record['bones'].values())):
            bone = armature.data.edit_bones.get(name)
            if bone:
                armature.data.edit_bones.remove(bone)
        _limb()._mode_set(context, armature, 'OBJECT')
    finally:
        armature.data.use_mirror_x = mirror_before
    for entry in record['widgets'].values():
        obj = bpy.data.objects.get(entry['object'])
        if obj:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if not mesh.users:
                bpy.data.meshes.remove(mesh)
    collection = bpy.data.collections.get(record['widget_collection'])
    if collection and not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)


def build(context, armature, key, toe_name=None, shoe=None):
    """Add one optional foot extension, preserving base target coordinates."""
    from . import bone_collections, limb_ik_fk
    _active(context, armature)
    side = _side(key)
    key = ('LEG', side)
    inventory = _limb()._validate_inventory(armature)
    if existing := get_record(armature, key):
        validate(armature, inventory)
        return existing
    rig = inventory['rigs'].get(key)
    if rig is None or inventory['schema'] not in {_limb().ROLL_DECOUPLED_SCHEMA, _limb().DIRECT_PREROLL_SCHEMA}:
        raise _error('Build a current leg IK before adding Foot Controls.')
    if rig.get('auto_offset_rotation') is None:
        raise _error('Rebuild this older leg to add its current end-rotation relation first.')
    toe = resolve_toe(armature, rig, toe_name)
    toe_pose = armature.pose.bones[toe.name]
    if toe_pose.constraints or _curve_mentions(armature, {toe.name}):
        raise _error('The toe already has animation, a driver, or another constraint. Keep it or remove that dependency before adding Toe Bend.')
    target = armature.pose.bones[rig['target'].name]
    native_foot = armature.pose.bones[rig['chain'][2]]
    _update(context, armature)
    visual_state, visual_metadata = _roll_visual_plan(context, armature, rig['chain'][2], toe.name, shoe)
    desired = {name: armature.pose.bones[name].matrix.copy() for name in (*rig['chain'], toe.name)}
    ankle, ball, tip = native_foot.head.copy(), toe_pose.head.copy(), toe_pose.tail.copy()
    if (tip - ball).length < 1e-5 or (ball - ankle).length < 1e-5:
        raise _error('The foot and toe need distinct ankle, ball and toe-tip positions.')
    up = (armature.matrix_world.to_3x3().inverted_safe() @ Vector((0, 0, 1))).normalized()
    forward = tip - ankle
    forward -= up * forward.dot(up)
    if forward.length < 1e-5:
        forward = tip - ball
        forward -= up * forward.dot(up)
    if forward.length < 1e-5:
        raise _error('Use a readable forward foot pose before adding Foot Controls.')
    forward.normalize()
    right = forward.cross(up).normalized()
    up = right.cross(forward).normalized()
    ground = Matrix((right, forward, up)).transposed()
    foot_length = (tip - ankle).length
    heel = ankle - forward * ((ball - ankle).length * 0.24)
    heel -= up * (heel - tip).dot(up)
    handle = heel - forward * foot_length * 0.30 + up * foot_length * 0.32
    depose_target = target.bone.matrix_local @ target.matrix.inverted_safe()
    depose_foot = native_foot.bone.matrix_local @ native_foot.matrix.inverted_safe()
    names = {role: prefix + '.' + side for role, prefix in (
        ('FOOT_ROLL', 'CTRL_foot_roll'), ('HEEL_PIVOT', 'MCH_foot_heel'),
        ('TOE_TIP_PIVOT', 'MCH_foot_tip'), ('BALL_PIVOT', 'MCH_foot_ball'),
        ('ANKLE_SOLVER', 'MCH_foot_ankle'), ('IK_TOE_REF', 'MCH_toe_IK_ref'),
        ('FK_TOE_REF', 'MCH_toe_FK_ref'), ('TOE_SPACE', 'MCH_toe_space'),
        ('TOE_BEND', 'CTRL_toe_bend'))}
    if any(name in armature.data.bones for name in names.values()):
        raise _error('A Foot Controls bone name already exists; rename that unrelated bone first.')
    record = {'version': VERSION, 'id': uuid.uuid4().hex, 'side': side, 'rig_id': rig['rig_id'],
              'chain': list(rig['chain']), 'toe': toe.name, 'target': target.name,
              'solver': names['ANKLE_SOLVER'], 'base_solver': rig['solver_target'].name,
              'legacy_heel': rig['heel'].name if rig.get('heel') else None,
              'roll': names['FOOT_ROLL'], 'toe_control': names['TOE_BEND'],
              'bones': names, 'bone_states': {}, 'widgets': {}, 'drivers': [], 'constraints': [],
              'original_constraints': [_constraint_state(owner, con) for owner, con, entry in rig['entries']
                                       if entry['role'] in {'IK', 'END_ROTATION', 'AUTO_OFFSET_ROTATION'}]}
    record['widget_collection'] = 'CD Foot Widgets ' + record['id'][:10]
    previous_records = _all_records(armature)
    layout_before = bone_collections.capture_managed_layout(armature)
    context_before = _limb()._capture_context(context, armature)
    pose_before = {pb.name: pb.matrix_basis.copy() for pb in armature.pose.bones}
    base_entries = [(owner.name, con.name, entry['role']) for owner, con, entry in rig['entries']]
    switching_added = limb_ik_fk.VERSION_KEY not in target.bone
    limb_ik_fk.ensure_switching(armature, inventory, keys=(key,))
    mirror_before = armature.data.use_mirror_x
    plans = [
        ('FOOT_ROLL', target.name, _frame(handle, ground), depose_target),
        ('HEEL_PIVOT', target.name, _frame(heel, ground), depose_target),
        ('TOE_TIP_PIVOT', names['HEEL_PIVOT'], _frame(tip, ground), depose_target),
        ('BALL_PIVOT', names['TOE_TIP_PIVOT'], _frame(ball, ground), depose_target),
        ('ANKLE_SOLVER', names['BALL_PIVOT'], desired[native_foot.name], depose_target),
        ('IK_TOE_REF', names['TOE_TIP_PIVOT'], desired[toe.name], depose_target),
        ('FK_TOE_REF', native_foot.name, desired[toe.name], depose_foot),
        ('TOE_SPACE', target.name, desired[toe.name], depose_target),
        ('TOE_BEND', names['TOE_SPACE'], desired[toe.name], depose_target),
    ]
    try:
        armature.data.use_mirror_x = False
        _limb()._mode_set(context, armature, 'EDIT')
        for role, parent_name, wanted, depose in plans:
            bone = armature.data.edit_bones.new(names[role])
            rest_matrix = depose @ wanted
            length = max((tip - ball).length if role == 'TOE_BEND' else foot_length * 0.16, 0.01)
            bone.head = rest_matrix.translation
            bone.tail = bone.head + rest_matrix.to_3x3().col[1].normalized() * length
            bone.align_roll(rest_matrix.to_3x3().col[2])
            bone.parent = armature.data.edit_bones[parent_name]
            bone.use_connect = False
            bone.use_deform = False
        _limb()._mode_set(context, armature, 'OBJECT')
        target = armature.pose.bones[record['target']]
        toe_pose = armature.pose.bones[record['toe']]
        for role, parent_name, wanted, _depose in plans:
            pb = armature.pose.bones[names[role]]
            _tag(pb.bone, record, role)
            pb.rotation_mode = 'XYZ'
            pb.bone.hide = role not in {'FOOT_ROLL', 'TOE_BEND'}
            pb.bone.hide_select = pb.bone.hide
            pb.lock_location = pb.lock_scale = (True, True, True)
            pb.lock_rotation = (False, False, True) if role == 'FOOT_ROLL' else (False, False, False) if role == 'TOE_BEND' else (True, True, True)
            pb.matrix = wanted
            _update(context, armature)
            record['bone_states'][role] = {'head': list(pb.bone.head_local), 'tail': list(pb.bone.tail_local),
                'matrix': _matrix(pb.bone.matrix_local), 'parent': parent_name}
        control_groups = [c for c in armature.data.collections_all
                          if c.get(_limb().OWNER_KEY) == _limb().OWNER_VALUE
                          and c.get(_limb().ROLE_KEY) == 'CONTROL_COLLECTION']
        if len(control_groups) != 1:
            raise _error('The existing leg Controls collection is missing or duplicated.')
        for name in names.values():
            control_groups[0].assign(armature.data.bones[name])
        _add_widget(context, armature, record, 'FOOT_ROLL', 'HEEL', foot_length * 0.32)
        _add_widget(context, armature, record, 'TOE_BEND', 'FINGER', (tip - ball).length * 1.46)
        roll = armature.pose.bones[record['roll']]
        x_path = roll.path_from_id('rotation_euler') + '[0]'
        y_path = roll.path_from_id('rotation_euler') + '[1]'
        for role, axis, expression, variables in (
            ('HEEL_PIVOT', 0, '-min(roll,0)', {'roll': x_path}),
            ('HEEL_PIVOT', 1, ('bank' if side == 'L' else '-bank'), {'bank': y_path}),
            ('BALL_PIVOT', 0, '-min(max(roll,0),0.7853981633974483)', {'roll': x_path}),
            ('TOE_TIP_PIVOT', 0, '-max(roll-0.7853981633974483,0)', {'roll': x_path}),
        ):
            record['drivers'].append(_driver(armature, armature.pose.bones[names[role]], 'rotation_euler', axis, expression, variables))
        space = armature.pose.bones[names['TOE_SPACE']]
        _add_constraint(record, space, 'COPY_TRANSFORMS', 'CD Foot FK Toe Space', armature,
                        subtarget=names['FK_TOE_REF'], target_space='WORLD', owner_space='WORLD', mix_mode='REPLACE')
        ik_space = _add_constraint(record, space, 'COPY_TRANSFORMS', 'CD Foot IK Toe Space', armature,
                        subtarget=names['IK_TOE_REF'], target_space='WORLD', owner_space='WORLD', mix_mode='REPLACE')
        record['drivers'].append(_driver(armature, ik_space, 'influence', -1, 'ik_fk', {'ik_fk': limb_ik_fk.property_path(target)}))
        _add_constraint(record, toe_pose, 'COPY_TRANSFORMS', 'CD Toe Bend', armature,
                        subtarget=record['toe_control'], target_space='WORLD', owner_space='WORLD', mix_mode='REPLACE')
        for owner_name, con_name, role in base_entries:
            if role in {'IK', 'END_ROTATION', 'AUTO_OFFSET_ROTATION'}:
                con = armature.pose.bones[owner_name].constraints[con_name]
                con.subtarget = record['solver']
                if role != 'IK':
                    con.target_space = con.owner_space = 'WORLD'
                    con.mix_mode = 'REPLACE'
        updated = dict(previous_records, **{side: record})
        _write_records(armature, updated)
        _update(context, armature)
        _verify_pose(armature, desired)
        validate(armature)
        _limb()._validate_inventory(armature)
        _set_roll_visual(armature, armature.pose.bones[record['roll']], visual_state)
        record['roll_visual_fit'] = visual_metadata
        _write_records(armature, dict(previous_records, **{side: record}))
        bone_collections.finish_rig_edit(armature, layout_before)
    except Exception:
        _set_fields(armature, record['original_constraints'])
        _write_records(armature, previous_records)
        _delete_graph(context, armature, record)
        if switching_added:
            for owner_name, con_name, role in base_entries:
                if owner_name in record['chain'] and role in {'IK', 'SOURCE_UPPER_ROTATION', 'SOURCE_LOWER_ROTATION', 'END_ROTATION', 'AUTO_OFFSET_ROTATION'}:
                    con = armature.pose.bones[owner_name].constraints[con_name]
                    con.driver_remove('influence')
                    con.influence = 1.0
            restored_target = armature.pose.bones[record['target']]
            restored_target.pop(limb_ik_fk.PROPERTY, None)
            restored_target.bone.pop(limb_ik_fk.VERSION_KEY, None)
        for name, basis in pose_before.items():
            if name in armature.pose.bones:
                armature.pose.bones[name].matrix_basis = basis
        bone_collections.restore_layout(armature, layout_before)
        _update(context, armature)
        raise
    finally:
        _limb()._restore_context(context, armature, context_before)
        armature.data.use_mirror_x = mirror_before
    from . import control_colors
    for name in (record['roll'], record['toe_control']):
        control_colors.style(armature.pose.bones[name])
    return record


def _driver_owners():
    seen = set()
    for prop in bpy.data.bl_rna.properties:
        if prop.type != 'COLLECTION':
            continue
        for owner in getattr(bpy.data, prop.identifier, ()):
            if not isinstance(owner, bpy.types.ID):
                continue
            for candidate in (owner, getattr(owner, 'node_tree', None)):
                if candidate is not None and candidate.as_pointer() not in seen:
                    seen.add(candidate.as_pointer())
                    yield candidate


def _refuse_foreign_dependencies(armature, record):
    names = set(record['bones'].values())
    own_paths = {(e['path'], e['index']) for e in record['drivers']}
    own_cons = {(e['owner'], e['name']) for e in record['constraints']}
    base_cons = {(e['owner'], e['name']) for e in record['original_constraints']}
    owned_widgets = {e['object']: record['bones'][role] for role, e in record['widgets'].items()}
    for obj in bpy.data.objects:
        if obj.parent == armature and obj.parent_type == 'BONE' and obj.parent_bone in names:
            raise _error(f"Object '{obj.name}' follows a Foot Controls bone; detach it before removal.")
        for con in obj.constraints:
            if _limb()._constraint_references_controls(con, armature, names):
                raise _error(f"Object constraint '{con.name}' follows Foot Controls; detach it before removal.")
        if obj.type != 'ARMATURE':
            continue
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if obj == armature and (pb.name, con.name) in own_cons | base_cons:
                    continue
                if _limb()._constraint_references_controls(con, armature, names):
                    raise _error(f"Constraint '{con.name}' follows Foot Controls; detach it before removal.")
            if (obj == armature and pb.name not in names and pb.custom_shape_transform
                    and pb.custom_shape_transform.name in names):
                raise _error('Another bone displays its shape in a Foot Controls frame; detach it before removal.')
            if pb.custom_shape and pb.custom_shape.name in owned_widgets:
                if obj != armature or pb.name != owned_widgets[pb.custom_shape.name]:
                    raise _error('Another bone uses a Foot Controls widget; give it an independent shape first.')
    for owner in _driver_owners():
        animation = getattr(owner, 'animation_data', None)
        for curve in animation.drivers if animation else ():
            if owner == armature and (curve.data_path, curve.array_index) in own_paths:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id == armature and (target.bone_target in names
                            or _limb()._path_mentions_bone(target.data_path, names)):
                        raise _error(f"A driver on '{owner.name}' reads Foot Controls; detach it before removal.")
    collection = bpy.data.collections[record['widget_collection']]
    if collection.children or collection.users > 1 or set(collection.objects.keys()) != set(owned_widgets):
        raise _error('The Foot Controls widget collection contains artist objects; keep them separate before removal.')
    for entry in record['widgets'].values():
        obj = bpy.data.objects[entry['object']]
        if obj.data.users != 1 or tuple(obj.users_collection) != (collection,) or obj.users > 2:
            raise _error('A Foot Controls widget is shared or linked elsewhere; make that use independent before removal.')


def remove(context, armature, key):
    """Remove one extension; match the original rig before deleting helpers."""
    from . import bone_collections, limb_ik_fk
    _active(context, armature)
    side = _side(key)
    key = ('LEG', side)
    inventory = _limb()._validate_inventory(armature)
    record = get_record(armature, key)
    if record is None:
        raise _error('This leg has no Foot Controls to remove.')
    validate(armature, inventory)
    _refuse_foreign_dependencies(armature, record)
    names = set(record['bones'].values())
    if _curve_mentions(armature, names, include_drivers=False):
        raise _error('Foot Controls has keyed animation. Keep it or remove those animation channels before removing its controls.')
    own_paths = {entry['path'] for entry in record['drivers']}
    if armature.animation_data:
        for curve in armature.animation_data.drivers:
            if _limb()._path_mentions_bone(curve.data_path, names) and curve.data_path not in own_paths:
                raise _error('An external driver uses Foot Controls; keep that setup intact.')
    for bone in armature.data.bones:
        if bone.name not in names and bone.parent and bone.parent.name in names:
            raise _error('Another bone is parented to Foot Controls; detach it first.')
    own_cons = {(entry['owner'], entry['name']) for entry in record['constraints']}
    base_cons = {(entry['owner'], entry['name']) for entry in record['original_constraints']}
    for obj in (o for o in bpy.data.objects if o.type == 'ARMATURE'):
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if obj is armature and (pb.name, con.name) in own_cons | base_cons:
                    continue
                if getattr(con, 'target', None) is armature and getattr(con, 'subtarget', None) in names:
                    raise _error('Another constraint follows Foot Controls; detach it first.')
    desired = {name: armature.pose.bones[name].matrix.copy() for name in (*record['chain'], record['toe'])}
    pose_before = {pb.name: pb.matrix_basis.copy() for pb in armature.pose.bones}
    layout_before = bone_collections.capture_managed_layout(armature)
    context_before = _limb()._capture_context(context, armature)
    extended_fields = [_constraint_state(armature.pose.bones[e['owner']], armature.pose.bones[e['owner']].constraints[e['name']])
                       for e in record['original_constraints']]
    native_toe = armature.pose.bones[record['toe']]
    toe_constraint = native_toe.constraints['CD Toe Bend']
    try:
        _SUSPENDED.add((armature.as_pointer(), side))
        _set_fields(armature, record['original_constraints'])
        toe_constraint.mute = True
        _update(context, armature)
        limb_ik_fk.match_existing_pose(context, armature, key, desired, keyframe=False)
        native_toe.matrix = desired[record['toe']]
        _update(context, armature)
        _verify_pose(armature, desired)
    except Exception:
        _set_fields(armature, extended_fields)
        toe_constraint.mute = False
        for name, basis in pose_before.items():
            armature.pose.bones[name].matrix_basis = basis
        _update(context, armature)
        raise
    finally:
        _SUSPENDED.discard((armature.as_pointer(), side))
    try:
        _delete_graph(context, armature, record)
        updated = _all_records(armature)
        del updated[side]
        _write_records(armature, updated)
        bone_collections.finish_rig_edit(armature, layout_before)
        _update(context, armature)
        _verify_pose(armature, desired)
    finally:
        _limb()._restore_context(context, armature, context_before)
    from . import control_colors
    control_colors.cleanup(armature)
    return {'side': side, 'bones_removed': len(names), 'pose_preserved': True}
