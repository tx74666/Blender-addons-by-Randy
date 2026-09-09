"""Optional native spine IK branch beside the existing FK and shared Bend graph.

The endpoint solver retains a matched posture as its input. Shape rotates that
input before IK solves it; it is deliberately not a pole that flattens S curves.
"""
from __future__ import annotations

import json
import math
import uuid

import bpy
from mathutils import Matrix, Vector

from . import torso_controls as torso
from .eye_controls import _controller_snapshot

OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'spine_ik_fk'
ROLE_KEY = 'character_designer_spine_ik_fk_role'
ID_KEY = 'character_designer_spine_ik_fk_id'
RECORD_KEY = 'character_designer_spine_ik_fk_v1'
VERSION = 1
PROPERTY = 'ik_fk'
_SOLVER_FIELDS = {'ik_stretch': 0.0, 'lock_ik_x': False, 'lock_ik_y': False, 'lock_ik_z': False,
                  'use_ik_limit_x': False, 'use_ik_limit_y': False, 'use_ik_limit_z': False,
                  'ik_stiffness_x': 0.0, 'ik_stiffness_y': 0.0, 'ik_stiffness_z': 0.0}


def _limb():
    from . import limb_ik
    return limb_ik


def _error(message):
    return _limb().LimbIKError(message)


def _update(context, armature):
    torso._update(context, armature)


def _tag(item, record, role):
    item[OWNER_KEY], item[ID_KEY], item[ROLE_KEY] = OWNER_VALUE, record['id'], role


def _owned(item, record, role):
    return (item.get(OWNER_KEY) == OWNER_VALUE and item.get(ID_KEY) == record['id']
            and item.get(ROLE_KEY) == role)


def get_record(armature):
    raw = armature.data.get(RECORD_KEY)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        count = len(record['sources'])
        roles = {'CHEST', 'SHAPE', *(f'IK_{i}' for i in range(count))}
        if (record['version'] != VERSION or count not in {3, 4} or len(set(record['sources'])) != count
                or set(record['bones']) != roles or set(record['bone_states']) != roles
                or len(set(record['bones'].values())) != count + 2
                or record['chest'] != record['bones']['CHEST'] or record['shape'] != record['bones']['SHAPE']
                or set(record['widgets']) != {'CHEST', 'SHAPE'}
                or set(record['source_states']) != {*record['sources'], record['hips']}
                or len(record['drivers']) != count or len(record['constraints']) != count * 2 + 1):
            raise ValueError('unsupported structure')
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error('Spine IK/FK recovery data is invalid; restore a saved copy.') from exc


def extra_constraints(armature):
    """Exact source-constraint exceptions for the torso validator; no recursion."""
    record = get_record(armature)
    return {(name, 'CD Spine IK') for name in record['sources']} if record else set()


def property_path(target):
    return target.path_from_id() + '["' + PROPERTY + '"]'


def _switch_metadata(target):
    target.id_properties_ui(PROPERTY).update(min=0.0, max=1.0,
        description='0 = existing FK/Bend, 1 = Chest IK; use Snap to preserve the pose')


def mode_for_rig(armature):
    record = get_record(armature)
    if record is None:
        return 'FK'
    value = armature.pose.bones[record['chest']].get(PROPERTY)
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
        raise _error('The Spine IK/FK value must lie between zero and one.')
    return 'IK' if value >= 1 - 1e-6 else 'FK' if value <= 1e-6 else 'BLEND'


def collection_members(armature):
    record = get_record(armature)
    if record is None:
        return {key: set() for key in ('generated', 'replaced', 'always', 'visible', 'hidden_fk')}
    mode = mode_for_rig(armature)
    visible = {record['chest'], record['shape']} if mode != 'FK' else set()
    return {'generated': set(record['bones'].values()), 'replaced': set(record['sources']),
            'always': visible, 'visible': visible,
            'hidden_fk': {record['bend'], *record['fk_controls'].values()} if mode == 'IK' else set()}


def _constraint_plan(record):
    count, names = len(record['sources']), record['bones']
    entries = []
    for i in range(count - 1):
        entries.append({'owner': names[f'IK_{i}'], 'name': 'CD Spine Shape', 'type': 'COPY_ROTATION',
                        'fields': {'subtarget': record['shape'], 'target_space': 'LOCAL',
                                   'owner_space': 'LOCAL', 'mix_mode': 'AFTER', 'influence': 1.0 / (count - 1)}})
    entries.append({'owner': names[f'IK_{count - 2}'], 'name': 'CD Spine Endpoint', 'type': 'IK',
                    'fields': {'subtarget': record['chest'], 'chain_count': count - 1, 'use_stretch': False,
                               'use_tail': True, 'use_rotation': False, 'iterations': 500, 'influence': 1.0,
                               'use_location': True, 'ik_type': 'COPY_POSE', 'weight': 1.0,
                               'reference_axis': 'BONE', 'pole_angle': 0.0,
                               'lock_location_x': True, 'lock_location_y': True, 'lock_location_z': True}})
    entries.append({'owner': names[f'IK_{count - 1}'], 'name': 'CD Spine Chest Rotation', 'type': 'COPY_ROTATION',
                    'fields': {'subtarget': record['chest'], 'target_space': 'WORLD',
                               'owner_space': 'WORLD', 'mix_mode': 'REPLACE', 'influence': 1.0}})
    for i, source in enumerate(record['sources']):
        entries.append({'owner': source, 'name': 'CD Spine IK', 'type': 'COPY_TRANSFORMS',
                        'fields': {'subtarget': names[f'IK_{i}'], 'target_space': 'WORLD',
                                   'owner_space': 'WORLD', 'mix_mode': 'REPLACE'}, 'driven': True})
    for entry in entries:
        if entry['type'] == 'COPY_ROTATION':
            entry['fields'].update(use_x=True, use_y=True, use_z=True, invert_x=False,
                                   invert_y=False, invert_z=False, euler_order='AUTO')
        elif entry['type'] == 'COPY_TRANSFORMS':
            entry['fields'].update(head_tail=0.0, use_bbone_shape=False, remove_target_shear=False)
    return entries


def _find_driver(armature, path):
    curves = [curve for curve in armature.animation_data.drivers if curve.data_path == path] if armature.animation_data else []
    if len(curves) > 1:
        raise _error('Spine IK/FK has duplicate influence drivers.')
    return curves[0] if curves else None


def _make_driver(armature, constraint, chest):
    curve = constraint.driver_add('influence')
    for modifier in tuple(curve.modifiers):
        curve.modifiers.remove(modifier)
    curve.keyframe_points.clear()
    for value in (0.0, 1.0):
        curve.keyframe_points.insert(value, value).interpolation = 'LINEAR'
    curve.extrapolation = 'LINEAR'
    driver = curve.driver
    driver.type, driver.expression = 'SCRIPTED', PROPERTY
    for variable in tuple(driver.variables):
        driver.variables.remove(variable)
    variable = driver.variables.new()
    variable.name, variable.type = PROPERTY, 'SINGLE_PROP'
    variable.targets[0].id, variable.targets[0].data_path = armature, property_path(chest)
    return {'owner': constraint.id_data.name, 'path': curve.data_path, 'index': curve.array_index}


def _valid_driver(armature, path, chest):
    curve = _find_driver(armature, path)
    if (curve is None or curve.array_index != 0 or curve.mute or curve.modifiers
            or curve.extrapolation != 'LINEAR' or len(curve.keyframe_points) != 2
            or any(tuple(point.co) != (float(i), float(i)) or point.interpolation != 'LINEAR'
                   for i, point in enumerate(curve.keyframe_points))):
        return False
    driver = curve.driver
    if driver.type != 'SCRIPTED' or driver.expression != PROPERTY or len(driver.variables) != 1:
        return False
    variable = driver.variables[0]
    return (variable.name == PROPERTY and variable.type == 'SINGLE_PROP'
            and variable.targets[0].id == armature and variable.targets[0].data_path == property_path(chest))


def validate(armature, inventory=None):
    record = get_record(armature)
    actual = {bone.name for bone in armature.data.bones if bone.get(OWNER_KEY) == OWNER_VALUE}
    if record is None:
        if actual:
            raise _error('Spine IK/FK bones have lost their recovery data.')
        return None
    try:
        original = torso.get_record(armature)
        if (original is None or original['id'] != record['torso_id'] or original['sources'] != record['sources']
                or original['controls'] != record['fk_controls'] or original['bend'] != record['bend']
                or set(original['bones'].values()) != set(record['torso_bones'])):
            raise _error('The original Spine Controls changed; preserve that setup before continuing.')
        if actual != set(record['bones'].values()):
            raise _error('Spine IK/FK bones were added, removed, or renamed.')
        for role, name in record['bones'].items():
            bone, pb = armature.data.bones[name], armature.pose.bones[name]
            if not _owned(bone, record, role) or bone.use_deform or not torso._same_rest(bone, record['bone_states'][role]):
                raise _error(f"Spine IK/FK bone '{name}' was structurally edited.")
            if role.startswith('IK_') and any(not torso._same_value(getattr(pb, key), value) for key, value in _SOLVER_FIELDS.items()):
                raise _error('The Spine IK solver limits or stretch settings were edited.')
        for name, state in record['source_states'].items():
            bone = armature.data.bones.get(name)
            if bone is None or not torso._same_rest(bone, state) or bone.use_deform != state['deform']:
                raise _error(f"Spine source '{name}' changed; restore its original structure first.")
        if record['constraints'] != _constraint_plan(record):
            raise _error('Spine IK/FK constraint recovery data was edited.')
        pairs = {(entry['owner'], entry['name']) for entry in record['constraints']}
        for name in record['bones'].values():
            if any((name, con.name) not in pairs for con in armature.pose.bones[name].constraints):
                raise _error('A Spine IK/FK helper has additional constraints; preserve them first.')
        chest = armature.pose.bones[record['chest']]
        mode_for_rig(armature)
        if _find_driver(armature, property_path(chest)) is not None:
            raise _error('The Spine IK/FK value is driven externally; preserve that setup first.')
        expected_paths = set()
        for entry in record['constraints']:
            pb = armature.pose.bones[entry['owner']]
            con = pb.constraints.get(entry['name'])
            if (con is None or con.type != entry['type'] or con.target != armature or con.mute
                    or any(not torso._same_value(getattr(con, key), value) for key, value in entry['fields'].items())):
                raise _error(f"Spine IK/FK constraint '{entry['name']}' was edited.")
            if con.type == 'IK' and (con.pole_target is not None or con.pole_subtarget):
                raise _error('The Spine IK solver was assigned a pole; restore its original settings.')
            if entry.get('driven'):
                path = con.path_from_id('influence')
                expected_paths.add(path)
                if not _valid_driver(armature, path, chest) or pb.constraints[-1] != con:
                    raise _error('A Spine IK/FK influence driver or constraint order was edited.')
        if (expected_paths != {entry['path'] for entry in record['drivers']}
                or any(entry.get('index') != 0 for entry in record['drivers'])):
            raise _error('Spine IK/FK driver recovery data was edited.')
        for role, entry in record['widgets'].items():
            obj = bpy.data.objects.get(entry['object'])
            pb = armature.pose.bones[record['bones'][role]]
            if (obj is None or obj.type != 'MESH' or not _owned(obj, record, 'WIDGET')
                    or obj.data.name != entry['mesh'] or not _owned(obj.data, record, 'WIDGET_MESH')
                    or pb.custom_shape != obj or pb.custom_shape_transform is not None):
                raise _error('A Spine IK/FK widget was replaced or removed.')
        collection = bpy.data.collections.get(record['widget_collection'])
        if collection is None or not _owned(collection, record, 'WIDGET_COLLECTION'):
            raise _error('The Spine IK/FK widget collection is missing.')
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise _error('Spine IK/FK recovery data no longer matches this rig.') from exc
    return record


def _guard_animation(context, armature, names, own_paths=(), *, keyframe=False):
    if keyframe or context.scene.tool_settings.use_keyframe_insert_auto:
        raise _error('Spine IK/FK matching does not yet key animation; turn off Auto Key before switching.')
    curves = [curve for action in _limb()._actions_for_id(armature) for curve in _limb()._fcurves_for_action(action)]
    if armature.animation_data:
        curves += [curve for curve in armature.animation_data.drivers if curve.data_path not in own_paths]
    if any(_limb()._path_mentions_bone(curve.data_path, names) for curve in curves):
        raise _error('This spine has animation or external drivers; preserve those channels before adding, switching, or removing Spine IK/FK.')


def _matrices(armature):
    generated = _limb().GENERATED_CONTROL_OWNERS | {OWNER_VALUE}
    return {pb.name: pb.matrix.copy() for pb in armature.pose.bones if pb.bone.get(OWNER_KEY) not in generated}


def _verify_pose(armature, desired):
    if any(not _limb()._matrix_is_finite(matrix) or not _limb()._matrix_is_finite(armature.pose.bones[name].matrix)
           for name, matrix in desired.items()):
        raise _error('Spine IK/FK cannot match a non-finite pose; no changes kept.')
    error = max((abs(armature.pose.bones[name].matrix[i][j] - matrix[i][j])
                 for name, matrix in desired.items() for i in range(4) for j in range(4)), default=0.0)
    if not math.isfinite(error) or error > 4e-4:
        raise _error(f'Spine IK/FK could not match this pose without a jump (matrix error {error:.4g}); no changes kept. Check stretch, disconnected segments or extra constraints.')
    return error


def _pose_snapshot(armature):
    return {pb.name: (pb.rotation_mode, pb.matrix_basis.copy()) for pb in armature.pose.bones}


def _restore_pose(context, armature, snapshot):
    for name, (mode, basis) in snapshot.items():
        if name in armature.pose.bones:
            armature.pose.bones[name].rotation_mode = mode
            armature.pose.bones[name].matrix_basis = basis
    _update(context, armature)


def _seed_ik(context, armature, record, desired):
    for parent, child in zip(record['sources'], record['sources'][1:]):
        tail = desired[parent] @ Vector((0.0, armature.data.bones[parent].length, 0.0))
        if (tail - desired[child].translation).length > 2e-5:
            raise _error('Spine IK/FK cannot match a disconnected posed spine without a jump; restore connected FK segment positions first.')
    constraints = [armature.pose.bones[entry['owner']].constraints[entry['name']]
                   for entry in record['constraints'] if not entry.get('driven')]
    for con in constraints:
        con.mute = True
    try:
        armature.pose.bones[record['shape']].matrix_basis = Matrix.Identity(4)
        _update(context, armature)
        armature.pose.bones[record['chest']].matrix = desired[record['sources'][-1]]
        _update(context, armature)
        for i, name in enumerate(record['sources']):
            armature.pose.bones[record['bones'][f'IK_{i}']].matrix = desired[name]
            _update(context, armature)
    finally:
        for con in constraints:
            con.mute = False
    _update(context, armature)


def _match_fk(context, armature, record, desired):
    armature.pose.bones[record['chest']][PROPERTY] = 0.0
    _update(context, armature)
    for source in record['sources']:
        armature.pose.bones[record['fk_controls'][source]].matrix = desired[source]
        _update(context, armature)


def _add_widget(context, armature, record, role, width):
    collection = bpy.data.collections.get(record['widget_collection'])
    if collection is None:
        collection = bpy.data.collections.new(record['widget_collection'])
        context.scene.collection.children.link(collection)
        _tag(collection, record, 'WIDGET_COLLECTION')
    count = 48 if role == 'CHEST' else 4
    vertices = [(math.cos(i * math.tau / count) * width, 0.0,
                 math.sin(i * math.tau / count) * width * (0.62 if role == 'CHEST' else 0.75)) for i in range(count)]
    name = 'WGT_CD_Spine_IK_' + role + '_' + record['id'][:10]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [(i, (i + 1) % count) for i in range(count)], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    _tag(obj, record, 'WIDGET')
    _tag(mesh, record, 'WIDGET_MESH')
    record['widgets'][role] = {'object': obj.name, 'mesh': mesh.name}
    obj.hide_render, obj.hide_select = True, True
    obj.hide_set(True)
    pb = armature.pose.bones[record['bones'][role]]
    pb.custom_shape, pb.use_custom_shape_bone_size = obj, False
    if hasattr(pb, 'custom_shape_wire_width'):
        pb.custom_shape_wire_width = 2.0


def _add_constraints(armature, record):
    for entry in record['constraints']:
        pb = armature.pose.bones[entry['owner']]
        con = pb.constraints.get(entry['name']) or pb.constraints.new(entry['type'])
        con.name, con.target, con.mute = entry['name'], armature, False
        for key, value in entry['fields'].items():
            setattr(con, key, value)
        if entry.get('driven'):
            _make_driver(armature, con, armature.pose.bones[record['chest']])


def _delete_widgets(record):
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


def _delete_graph(context, armature, record, *, keep_widgets=False):
    paths = {entry['path'] for entry in record['drivers']}
    if armature.animation_data:
        for curve in tuple(armature.animation_data.drivers):
            if curve.data_path in paths:
                armature.animation_data.drivers.remove(curve)
    for entry in reversed(record['constraints']):
        pb = armature.pose.bones.get(entry['owner'])
        con = pb.constraints.get(entry['name']) if pb else None
        if con:
            pb.constraints.remove(con)
    _limb()._mode_set(context, armature, 'EDIT')
    for name in reversed(tuple(record['bones'].values())):
        bone = armature.data.edit_bones.get(name)
        if bone:
            armature.data.edit_bones.remove(bone)
    _limb()._mode_set(context, armature, 'OBJECT')
    if not keep_widgets:
        _delete_widgets(record)


def build(context, armature):
    """Add a separate IK branch in FK mode, retaining existing authored controls."""
    from . import bone_collections, control_colors
    torso._active(context, armature)
    if previous := validate(armature):
        return previous
    original = torso.validate(armature)
    if original is None:
        raise _error('Add Spine Controls before adding Spine IK/FK.')
    sources = original['sources']
    _guard_animation(context, armature, set(sources) | set(original['bones'].values()))
    for first, second in zip(sources, sources[1:]):
        if (armature.data.bones[first].tail_local - armature.data.bones[second].head_local).length > 1e-5:
            raise _error('Spine IK/FK needs consecutive segments whose heads meet the preceding tails.')
    groups = [group for group in armature.data.collections_all
              if group.get(_limb().OWNER_KEY) == _limb().OWNER_VALUE and group.get(_limb().ROLE_KEY) == 'CONTROL_COLLECTION']
    if len(groups) != 1:
        raise _error('The shared Controls collection is missing or duplicated.')
    names = {'CHEST': 'CTRL_spine_IK', 'SHAPE': 'CTRL_spine_shape'}
    names.update({f'IK_{i}': f'MCH_spine_IK_{i + 1}' for i in range(len(sources))})
    if any(name in armature.data.bones for name in names.values()):
        raise _error('A Spine IK/FK bone name is already occupied.')
    _update(context, armature)
    desired = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    before = _pose_snapshot(armature)
    record = {'version': VERSION, 'id': uuid.uuid4().hex, 'torso_id': original['id'],
              'sources': sources, 'hips': original['hips'], 'fk_controls': original['controls'],
              'bend': original['bend'], 'torso_bones': list(original['bones'].values()),
              'chest': names['CHEST'], 'shape': names['SHAPE'], 'bones': names, 'bone_states': {},
              'source_states': {name: torso._state(armature.data.bones[name]) for name in (*sources, original['hips'])},
              'widgets': {}, 'drivers': []}
    record['constraints'] = _constraint_plan(record)
    record['widget_collection'] = 'CD_Spine_IK_Widgets_' + record['id'][:10]
    layout, ctx = bone_collections.capture_managed_layout(armature), _limb()._capture_context(context, armature)
    mirror = armature.data.use_mirror_x
    length = sum(armature.data.bones[name].length for name in sources)
    plans = [('CHEST', original['hips'], sources[-1]), ('SHAPE', original['hips'], sources[len(sources)//2])]
    plans += [(f'IK_{i}', names[f'IK_{i - 1}'] if i else original['hips'], source) for i, source in enumerate(sources)]
    try:
        armature.data.use_mirror_x = False
        _limb()._mode_set(context, armature, 'EDIT')
        for role, parent, source in plans:
            state = record['source_states'][source]
            bone = armature.data.edit_bones.new(names[role])
            bone.head, bone.tail = state['head'], state['tail']
            bone.align_roll(Matrix(state['matrix']).to_3x3().col[2])
            bone.parent = armature.data.edit_bones[parent]
            bone.use_connect = role.startswith('IK_') and role != 'IK_0'
            bone.use_deform = False
            bone.inherit_scale = state['inherit_scale']
        _limb()._mode_set(context, armature, 'OBJECT')
        for role, name in names.items():
            pb = armature.pose.bones[name]
            _tag(pb.bone, record, role)
            hidden = role.startswith('IK_')
            pb.bone.hide = pb.bone.hide_select = hidden
            pb.rotation_mode = 'XYZ'
            pb.lock_location = (role != 'CHEST',) * 3
            pb.lock_rotation, pb.lock_scale = (hidden,) * 3, (True,) * 3
            if hidden:
                for key, value in _SOLVER_FIELDS.items():
                    setattr(pb, key, value)
            record['bone_states'][role] = torso._state(pb.bone)
            groups[0].assign(pb.bone)
        chest = armature.pose.bones[record['chest']]
        chest[PROPERTY] = 0.0
        _switch_metadata(chest)
        # Save paths before driver construction so partial construction rolls back.
        for entry in record['constraints']:
            if entry.get('driven'):
                escaped = _limb()._owned_constraint_path(entry['owner'], entry['name'])
                record['drivers'].append({'path': escaped + '.influence', 'index': 0})
        _add_constraints(armature, record)
        _seed_ik(context, armature, record, desired)
        _add_widget(context, armature, record, 'CHEST', length * 0.48)
        _add_widget(context, armature, record, 'SHAPE', length * 0.30)
        armature.data[RECORD_KEY] = json.dumps(record)
        _update(context, armature)
        _verify_pose(armature, desired)
        validate(armature)
        bone_collections.finish_rig_edit(armature, layout)
    except Exception:
        armature.data.pop(RECORD_KEY, None)
        _delete_graph(context, armature, record)
        _restore_pose(context, armature, before)
        bone_collections.restore_layout(armature, layout)
        raise
    finally:
        armature.data.use_mirror_x = mirror
        _limb()._restore_context(context, armature, ctx)
    for name in (record['chest'], record['shape']):
        control_colors.style(armature.pose.bones[name])
    return record


def switch(context, armature, mode, *, keyframe=False):
    """Match IK or FK and verify every native bone; roll back any unsupported pose."""
    from . import bone_collections
    torso._active(context, armature)
    if mode not in {'IK', 'FK'}:
        raise _error('Choose Spine IK or FK.')
    record = validate(armature)
    if record is None:
        raise _error('Add Spine IK/FK first.')
    torso.validate(armature)
    names = set(record['sources']) | set(record['torso_bones']) | set(record['bones'].values())
    _guard_animation(context, armature, names, {entry['path'] for entry in record['drivers']}, keyframe=keyframe)
    old_mode = mode_for_rig(armature)
    if old_mode == mode:
        return {'mode': mode, 'changed': False, 'keyed': False, 'error': 0.0}
    _update(context, armature)
    desired, before = _matrices(armature), _pose_snapshot(armature)
    chest, value = armature.pose.bones[record['chest']], armature.pose.bones[record['chest']][PROPERTY]
    layout = bone_collections.capture_managed_layout(armature)
    try:
        if mode == 'IK':
            _seed_ik(context, armature, record, desired)
            chest[PROPERTY] = 1.0
            _update(context, armature)
        else:
            _match_fk(context, armature, record, desired)
        error = _verify_pose(armature, desired)
        validate(armature)
        torso.validate(armature)
        bone_collections.finish_rig_edit(armature, layout)
    except Exception:
        chest[PROPERTY] = value
        _restore_pose(context, armature, before)
        bone_collections.restore_layout(armature, layout)
        raise
    return {'mode': mode, 'changed': True, 'keyed': False, 'error': error}


def _neutral_matrices(armature, record):
    """Native rest transforms transported by the current Hips pose and bone inheritance."""
    parent = armature.pose.bones[record['hips']].matrix.copy()
    result = {}
    for name in record['sources']:
        bone = armature.data.bones[name]
        parent = bone.convert_local_to_pose(Matrix.Identity(4), bone.matrix_local,
            parent_matrix=parent, parent_matrix_local=bone.parent.matrix_local)
        result[name] = parent.copy()
    return result


def reset(context, armature):
    """Explicitly reset both spine branches to native neutral under the current Hips.

    Snap preserves a posed IK input, so clearing only its visible controls need
    not remove that posture. This action also clears the existing torso offsets
    and the matched IK inputs. It never runs implicitly while adding or switching.
    """
    from . import bone_collections
    torso._active(context, armature)
    record = validate(armature)
    if record is None:
        raise _error('Add Spine IK/FK before using Reset Spine Pose.')
    torso.validate(armature)
    affected = set(record['torso_bones']) | set(record['bones'].values())
    _guard_animation(context, armature, affected | set(record['sources']),
                     {entry['path'] for entry in record['drivers']})
    _update(context, armature)
    before = _pose_snapshot(armature)
    desired = _neutral_matrices(armature, record)
    desired[record['hips']] = armature.pose.bones[record['hips']].matrix.copy()
    value = armature.pose.bones[record['chest']][PROPERTY]
    layout = bone_collections.capture_managed_layout(armature)
    try:
        for name in affected:
            armature.pose.bones[name].matrix_basis = Matrix.Identity(4)
        _update(context, armature)
        error = _verify_pose(armature, desired)
        for name, (_mode, basis) in before.items():
            if name not in affected and max(abs(armature.pose.bones[name].matrix_basis[i][j] - basis[i][j])
                                           for i in range(4) for j in range(4)) > 2e-6:
                raise _error('Reset Spine Pose could not preserve unrelated pose channels; no changes kept.')
        validate(armature)
        torso.validate(armature)
        bone_collections.finish_rig_edit(armature, layout)
    except Exception:
        armature.pose.bones[record['chest']][PROPERTY] = value
        _restore_pose(context, armature, before)
        bone_collections.restore_layout(armature, layout)
        raise
    return {'mode': mode_for_rig(armature), 'changed': True, 'keyed': False,
            'error': error, 'native_neutral': True}


def _refuse_dependencies(armature, record):
    from .foot_controls import _driver_owners
    names = set(record['bones'].values())
    for bone in armature.data.bones:
        if bone.name not in names and bone.parent and bone.parent.name in names:
            raise _error('Another bone follows Spine IK/FK; detach it before removal.')
    pairs = {(entry['owner'], entry['name']) for entry in record['constraints']}
    widgets = {entry['object']: record['bones'][role] for role, entry in record['widgets'].items()}
    paths = {entry['path'] for entry in record['drivers']}
    for obj in bpy.data.objects:
        if obj.parent == armature and obj.parent_type == 'BONE' and obj.parent_bone in names:
            raise _error('An object follows Spine IK/FK; detach it before removal.')
        for con in obj.constraints:
            if _limb()._constraint_references_controls(con, armature, names):
                raise _error('An object constraint follows Spine IK/FK; detach it before removal.')
        if obj.type != 'ARMATURE':
            continue
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if obj == armature and (pb.name, con.name) in pairs:
                    continue
                if _limb()._constraint_references_controls(con, armature, names):
                    raise _error('Another constraint follows Spine IK/FK; detach it before removal.')
            if obj == armature and pb.name not in names and pb.custom_shape_transform and pb.custom_shape_transform.name in names:
                raise _error('Another widget follows Spine IK/FK; detach it before removal.')
            if pb.custom_shape and pb.custom_shape.name in widgets and (obj != armature or pb.name != widgets[pb.custom_shape.name]):
                raise _error('A Spine IK/FK widget is shared; make that use independent first.')
    for owner in _driver_owners():
        animation = getattr(owner, 'animation_data', None)
        for curve in animation.drivers if animation else ():
            if owner == armature and curve.data_path in paths:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id == armature and (target.bone_target in names or _limb()._path_mentions_bone(target.data_path, names)):
                        raise _error('An external driver reads Spine IK/FK; preserve that setup first.')
    collection = bpy.data.collections[record['widget_collection']]
    if collection.children or collection.users > 1 or set(collection.objects.keys()) != set(widgets):
        raise _error('The Spine IK/FK widget collection contains artist data; separate it before removal.')
    for entry in record['widgets'].values():
        obj = bpy.data.objects[entry['object']]
        if obj.data.users != 1 or tuple(obj.users_collection) != (collection,) or obj.users > 2:
            raise _error('A Spine IK/FK widget is shared; make that use independent first.')


def _restore_graph(context, armature, record, snapshot):
    _limb()._mode_set(context, armature, 'EDIT')
    for role, name in record['bones'].items():
        state = record['bone_states'][role]
        bone = armature.data.edit_bones.get(name) or armature.data.edit_bones.new(name)
        bone.head, bone.tail = state['head'], state['tail']
        bone.align_roll(Matrix(state['matrix']).to_3x3().col[2])
        bone.parent = armature.data.edit_bones[state['parent']]
        bone.use_connect, bone.use_deform = state['connect'], False
        bone.inherit_scale, bone.use_inherit_rotation = state['inherit_scale'], state['inherit_rotation']
        bone.use_local_location = state['local_location']
    _limb()._mode_set(context, armature, 'OBJECT')
    for role, name in record['bones'].items():
        pb, state = armature.pose.bones[name], snapshot[role]
        for item, properties in ((pb.bone, state['bone_properties']), (pb, state['pose_properties'])):
            for key, value in properties.items():
                item[key] = value
        pb.rotation_mode, pb.matrix_basis = state['rotation_mode'], state['basis']
        pb.bone.hide, pb.bone.hide_select = state['hide'], state['hide_select']
        for field, value in state['fields'].items():
            setattr(pb, field, value)
        if role.startswith('IK_'):
            for field, value in _SOLVER_FIELDS.items():
                setattr(pb, field, value)
        if role in record['widgets']:
            pb.custom_shape = bpy.data.objects[record['widgets'][role]['object']]
        for item, (palette, colored_constraints, colors) in zip((pb.bone, pb), state['colors']):
            item.color.palette = palette
            item.color.custom.show_colored_constraints = colored_constraints
            for field, color in zip(('normal', 'select', 'active'), colors):
                setattr(item.color.custom, field, color)
    _switch_metadata(armature.pose.bones[record['chest']])
    _add_constraints(armature, record)
    armature.data[RECORD_KEY] = json.dumps(record)


def remove(context, armature):
    """Match the existing FK controls, then delete only this optional IK branch."""
    from . import bone_collections, control_colors
    torso._active(context, armature)
    record = validate(armature)
    if record is None:
        raise _error('This armature has no Spine IK/FK extension to remove.')
    torso.validate(armature)
    names = set(record['sources']) | set(record['torso_bones']) | set(record['bones'].values())
    _guard_animation(context, armature, names, {entry['path'] for entry in record['drivers']})
    _refuse_dependencies(armature, record)
    _update(context, armature)
    desired, before = _matrices(armature), _pose_snapshot(armature)
    value = armature.pose.bones[record['chest']][PROPERTY]
    controllers = _controller_snapshot(armature, record)
    layout, ctx = bone_collections.capture_managed_layout(armature), _limb()._capture_context(context, armature)
    mirror = armature.data.use_mirror_x
    try:
        _match_fk(context, armature, record, desired)
        _verify_pose(armature, desired)
    except Exception:
        armature.pose.bones[record['chest']][PROPERTY] = value
        _restore_pose(context, armature, before)
        raise
    try:
        armature.data.use_mirror_x = False
        _delete_graph(context, armature, record, keep_widgets=True)
        del armature.data[RECORD_KEY]
        bone_collections.finish_rig_edit(armature, layout)
        _update(context, armature)
        _verify_pose(armature, desired)
    except Exception:
        _restore_graph(context, armature, record, controllers)
        _restore_pose(context, armature, before)
        bone_collections.restore_layout(armature, layout)
        raise
    finally:
        armature.data.use_mirror_x = mirror
        _limb()._restore_context(context, armature, ctx)
    _delete_widgets(record)
    control_colors.cleanup(armature)
    return {'bones_removed': len(record['bones']), 'pose_preserved': True, 'mode': 'FK'}
