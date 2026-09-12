"""In-place checkpoints for the synchronous, explicitly requested Body operation.

This is not a general scene undo system. Callers must preflight their complete
plan (especially animation and external references) before deleting resources.
Native Objects, Armatures and Meshes are never substituted. Only deleted owned
helper bones/constraints/widgets may be recreated during rollback.
"""
import bpy
from mathutils import Matrix, Vector


def _modules():
    from . import widget_collections
    return widget_collections._modules()


def _owners():
    return {module.OWNER_VALUE for module in _modules()} | {'widget_collections'}


def _pointer(value):
    if value is None:
        return None
    try:
        return value.as_pointer()
    except ReferenceError:
        return None


def _encode(value):
    if isinstance(value, bpy.types.ID):
        return ('@ID', value.__class__.__name__, value.name, _pointer(value), value)
    if isinstance(value, bpy.types.PoseBone):
        return ('@POSE', value.id_data.name, value.name)
    if isinstance(value, bpy.types.Bone):
        return ('@BONE', value.id_data.name, value.name)
    if hasattr(value, 'to_dict'):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return [_encode(item) for item in value]
    except TypeError as exc:
        raise ValueError(f'Unsupported transaction value: {type(value).__name__}') from exc


def _decode(value, remap):
    if isinstance(value, tuple) and value and value[0] == '@ID':
        _, _kind, name, pointer, original = value
        if pointer in remap:
            return remap[pointer]
        if _pointer(original) == pointer:
            return original
        raise ValueError(f"Original referenced datablock '{name}' disappeared.")
    if isinstance(value, tuple) and value and value[0] in {'@POSE', '@BONE'}:
        if value[0] == '@POSE':
            return bpy.data.objects[value[1]].pose.bones[value[2]]
        return bpy.data.armatures[value[1]].bones[value[2]]
    if isinstance(value, dict):
        return {key: _decode(item, remap) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item, remap) for item in value]
    return value


def _properties(owner):
    values, ui = {}, {}
    try:
        keys = tuple(owner.keys())
    except TypeError:
        return values, ui
    for key in keys:
        values[key] = _encode(owner[key])
        try:
            ui[key] = owner.id_properties_ui(key).as_dict()
        except (TypeError, RuntimeError):
            pass
    return values, ui


def _set_properties(owner, state, remap):
    values, ui = state
    try:
        keys = tuple(owner.keys())
    except TypeError:
        if values:
            raise
        return
    for key in keys:
        if key not in values:
            del owner[key]
    for key, value in values.items():
        owner[key] = _decode(value, remap)
        if key in ui:
            owner.id_properties_ui(key).update(**ui[key])


def _rna(owner, exclude=()):
    result = {}
    for prop in owner.bl_rna.properties:
        name = prop.identifier
        if name in exclude or name == 'rna_type' or prop.is_readonly or prop.type == 'COLLECTION':
            continue
        value = getattr(owner, name)
        if prop.type == 'POINTER' and value is not None and not isinstance(value, (bpy.types.ID, bpy.types.PoseBone, bpy.types.Bone)):
            continue
        result[name] = _encode(value)
    return result


def _set_rna(owner, values, remap, *, exclude=()):
    # Mode first: later Euler/quaternion channels must not be interpreted in the
    # transient mode a component may have used while matching its pose.
    if 'rotation_mode' in values and 'rotation_mode' not in exclude:
        owner.rotation_mode = values['rotation_mode']
    for name, value in values.items():
        if name not in exclude and name != 'rotation_mode':
            if _encode(getattr(owner, name)) == value:
                continue
            resolved = _decode(value, remap)
            if resolved is None and getattr(owner, name) is None:
                continue
            setattr(owner, name, resolved)


def _constraint(con):
    return {'type': con.type, 'name': con.name, 'rna': _rna(con),
            'properties': _properties(con),
            'targets': [_rna(target) for target in getattr(con, 'targets', ())]}


def _constraints(owner, states, remap):
    expected = {state['name']: state for state in states}
    for con in tuple(owner.constraints):
        if con.name not in expected or con.type != expected[con.name]['type']:
            owner.constraints.remove(con)
    for index, state in enumerate(states):
        con = owner.constraints.get(state['name'])
        if con is None:
            con = owner.constraints.new(state['type'])
            con.name = state['name']
        for field in ('target', 'subtarget', 'pole_target', 'pole_subtarget', 'space_object', 'space_subtarget'):
            if field in state['rna']:
                setattr(con, field, _decode(state['rna'][field], remap))
        _set_rna(con, state['rna'], remap)
        _set_properties(con, state['properties'], remap)
        if hasattr(con, 'targets'):
            while len(con.targets) > len(state['targets']):
                con.targets.remove(con.targets[-1])
            while len(con.targets) < len(state['targets']):
                con.targets.new()
            for target, values in zip(con.targets, state['targets']):
                _set_rna(target, values, remap)
        position = list(owner.constraints).index(con)
        if position != index:
            owner.constraints.move(position, index)


def _driver(curve):
    return {'path': curve.data_path, 'index': curve.array_index,
            'rna': _rna(curve, {'data_path', 'array_index'}),
            'driver': _rna(curve.driver),
            'variables': [{'name': var.name, 'type': var.type,
                           'targets': [_rna(target) for target in var.targets]}
                          for var in curve.driver.variables],
            'keys': [_rna(key) for key in curve.keyframe_points],
            'modifiers': [{'type': mod.type, 'rna': _rna(mod)} for mod in curve.modifiers]}


def _drivers(owner):
    animation = owner.animation_data
    return [_driver(curve) for curve in animation.drivers] if animation else []


def _set_drivers(owner, states, remap):
    wanted = {(state['path'], state['index']): state for state in states}
    animation = owner.animation_data
    if animation:
        for curve in tuple(animation.drivers):
            if (curve.data_path, curve.array_index) not in wanted:
                animation.drivers.remove(curve)
    for state in states:
        animation = owner.animation_data_create()
        curve = next((curve for curve in animation.drivers
                      if (curve.data_path, curve.array_index) == (state['path'], state['index'])), None)
        if curve is None:
            curve = animation.drivers.new(state['path'], index=state['index'])
        _set_rna(curve, state['rna'], remap)
        _set_rna(curve.driver, state['driver'], remap)
        for var in tuple(curve.driver.variables):
            curve.driver.variables.remove(var)
        for state_var in state['variables']:
            var = curve.driver.variables.new()
            var.type, var.name = state_var['type'], state_var['name']
            for target, values in zip(var.targets, state_var['targets']):
                # id_type must precede id when the target is an Armature/Mesh ID.
                if 'id_type' in values:
                    target.id_type = values['id_type']
                _set_rna(target, values, remap, exclude={'id_type'})
        while curve.keyframe_points:
            curve.keyframe_points.remove(curve.keyframe_points[-1], fast=True)
        curve.keyframe_points.add(len(state['keys']))
        for point, values in zip(curve.keyframe_points, state['keys']):
            _set_rna(point, values, remap)
        for mod in tuple(curve.modifiers):
            curve.modifiers.remove(mod)
        for state_mod in state['modifiers']:
            mod = curve.modifiers.new(state_mod['type'])
            _set_rna(mod, state_mod['rna'], remap)
        curve.update()


_POSE_EXCLUDE = {'name', 'matrix', 'matrix_channel', 'matrix_basis', 'custom_shape',
                 'custom_shape_transform', 'bbone_custom_handle_start', 'bbone_custom_handle_end'}
_BONE_EXCLUDE = {'name', 'parent', 'head', 'tail', 'head_local', 'tail_local', 'matrix_local',
                 'bbone_custom_handle_start', 'bbone_custom_handle_end'}
_OBJECT_EXCLUDE = {'name', 'data', 'matrix_world', 'matrix_local', 'matrix_basis',
                   'dimensions', 'active_material', 'active_material_index'}


def _bone(rig, bone):
    from . import control_colors
    pb = rig.pose.bones[bone.name]
    return {'name': bone.name, 'native': bone.get('character_designer_owner') not in _owners(),
            'head': list(bone.head_local), 'tail': list(bone.tail_local),
            'z': list(bone.matrix_local.to_3x3().col[2]),
            'parent': bone.parent.name if bone.parent else None,
            'connect': bone.use_connect,
            'bone': _rna(bone, _BONE_EXCLUDE), 'bone_properties': _properties(bone),
            'pose': _rna(pb, _POSE_EXCLUDE), 'pose_properties': _properties(pb),
            'basis': [list(row) for row in pb.matrix_basis],
            'constraints': [_constraint(con) for con in pb.constraints],
            'colors': control_colors.capture_bone(pb),
            'pointers': {name: _encode(getattr(pb, name)) for name in
                         ('custom_shape', 'custom_shape_transform')},
            'bone_pointers': {name: _encode(getattr(bone, name)) for name in
                              ('bbone_custom_handle_start', 'bbone_custom_handle_end')}}


def capture(context, rig):
    """Capture one preflighted Body operation; discard the result on commit.

    The temporary mesh copies preserve edited geometry, attributes and materials
    without adding users to an original widget or changing dependency guards.
    """
    from . import limb_ik, bone_collections, widget_collections
    if rig.type != 'ARMATURE' or rig.library or rig.data.library or rig.data.users != 1 or rig.mode == 'EDIT':
        raise ValueError('Body transactions need a local single-user armature outside Edit Mode.')
    resources = widget_collections._resources(rig)
    leaves = {entry['collection'] for entry in resources}
    folders = set(leaves)
    todo = list(leaves)
    while todo:
        for parent in widget_collections._parents(todo.pop()):
            if parent.get('character_designer_owner') == widget_collections.OWNER_VALUE and parent not in folders:
                folders.add(parent)
                todo.append(parent)
    widget_names = {name for entry in resources for name in entry['objects']}
    state = {'rig': rig, 'data': rig.data, 'rig_pointer': rig.as_pointer(), 'data_pointer': rig.data.as_pointer(),
             'context': limb_ik._capture_context(context, rig),
             'object': _rna(rig, _OBJECT_EXCLUDE), 'properties': _properties(rig),
             'data_rna': _rna(rig.data, {'name'}), 'data_properties': _properties(rig.data),
             'drivers': _drivers(rig), 'data_drivers': _drivers(rig.data),
             'had_animation': rig.animation_data is not None,
             'data_had_animation': rig.data.animation_data is not None,
             'constraints': [_constraint(con) for con in rig.constraints],
             'bones': {bone.name: _bone(rig, bone) for bone in rig.data.bones},
             'layout': _encode(bone_collections.snapshot_layout(rig)),
             'objects_before': {obj.as_pointer() for obj in bpy.data.objects},
             'meshes_before': {mesh.as_pointer() for mesh in bpy.data.meshes},
             'collections_before': {collection.as_pointer() for collection in bpy.data.collections},
             'native_ids': [(obj, obj.as_pointer(), obj.data, _pointer(obj.data))
                            for obj in bpy.data.objects if obj.name not in widget_names],
             'folders': [], 'widgets': [], 'backups': [], 'closed': False}
    from . import forearm_twist
    state['corrective_records'] = [(obj, obj[forearm_twist.RECORD_KEY]) for obj in bpy.data.objects
                                   if obj.type == 'MESH' and forearm_twist.RECORD_KEY in obj
                                   and any(record.get('armature') == rig.name
                                           for record in forearm_twist._records(obj).values())]
    try:
        for collection in folders:
            state['folders'].append({'id': collection, 'pointer': collection.as_pointer(), 'name': collection.name,
                                     'rna': _rna(collection, {'name'}), 'properties': _properties(collection),
                                     'parents': [_encode(parent) for parent in widget_collections._parents(collection)],
                                     'layers': widget_collections._layer_states(collection)})
        for name in sorted(widget_names):
            obj = bpy.data.objects[name]
            if obj.type != 'MESH' or obj.modifiers or obj.constraints or obj.animation_data or obj.data.animation_data or obj.data.shape_keys:
                raise ValueError(f"Widget '{name}' has modifiers, animation or constraints; preserve that artist setup first.")
            backup = obj.data.copy()
            backup.name = '__CD_BODY_TRANSACTION__' + obj.data.name
            mesh_properties = _properties(obj.data)
            for property_name in tuple(backup.keys()):
                del backup[property_name]
            state['backups'].append(backup)
            state['widgets'].append({'id': obj, 'pointer': obj.as_pointer(), 'name': name,
                                     'mesh': obj.data, 'mesh_pointer': obj.data.as_pointer(),
                                     'mesh_name': obj.data.name, 'backup': backup,
                                     'mesh_properties': mesh_properties,
                                     'rna': _rna(obj, _OBJECT_EXCLUDE), 'properties': _properties(obj),
                                     'collections': [_encode(c) for c in obj.users_collection],
                                     'hidden': [(view, obj.hide_get(view_layer=view))
                                                for scene in bpy.data.scenes for view in scene.view_layers
                                                if name in view.objects]})
        return state
    except Exception:
        discard(state)
        raise


def assert_original_ids(snapshot):
    """Fail loudly if an operation replaced a native object or its original data."""
    rig = snapshot['rig']
    if _pointer(rig) != snapshot['rig_pointer'] or _pointer(rig.data) != snapshot['data_pointer']:
        raise ValueError('The original character armature was replaced during Body setup.')
    for obj, pointer, data, data_pointer in snapshot['native_ids']:
        if _pointer(obj) != pointer or _pointer(obj.data) != data_pointer:
            raise ValueError('An original object or its data was replaced during Body setup.')
    return True


def _mesh_geometry(mesh):
    return {'vertices': [tuple(v.co) for v in mesh.vertices],
            'edges': [tuple(edge.vertices) for edge in mesh.edges],
            'faces': [tuple(face.vertices) for face in mesh.polygons],
            'attributes': sorted((attribute.name, attribute.data_type, attribute.domain,
                                  [_rna(item) for item in attribute.data]) for attribute in mesh.attributes),
            'smooth': [face.use_smooth for face in mesh.polygons],
            'materials': [face.material_index for face in mesh.polygons]}


def _restore_mesh_geometry(mesh, backup):
    """Restore edited display geometry into a surviving Mesh, retaining its ID."""
    geometry = _mesh_geometry(backup)
    if _mesh_geometry(mesh) == geometry:
        return
    mesh.clear_geometry()
    mesh.from_pydata(geometry['vertices'], geometry['edges'], geometry['faces'])
    expected = {name for name, _type, _domain, _values in geometry['attributes']}
    for attribute in tuple(mesh.attributes):
        if attribute.name not in expected and not attribute.is_required:
            mesh.attributes.remove(attribute)
    for name, data_type, domain, values in geometry['attributes']:
        attribute = mesh.attributes.get(name)
        if attribute is not None and (attribute.data_type != data_type or attribute.domain != domain):
            mesh.attributes.remove(attribute)
            attribute = None
        if attribute is None:
            attribute = mesh.attributes.new(name, data_type, domain)
        for item, saved in zip(attribute.data, values):
            _set_rna(item, saved, {})
    for face, smooth, material in zip(mesh.polygons, geometry['smooth'], geometry['materials']):
        if face.use_smooth != smooth:
            face.use_smooth = smooth
        if face.material_index != material:
            face.material_index = material
    mesh.update()


def _restore_resources(snapshot):
    remap = {}
    for saved in snapshot['folders']:
        collection = saved['id'] if _pointer(saved['id']) == saved['pointer'] else None
        if collection is None:
            if saved['name'] in bpy.data.collections:
                raise ValueError(f"Cannot recover occupied collection '{saved['name']}'.")
            collection = bpy.data.collections.new(saved['name'])
        collection.name = saved['name']
        remap[saved['pointer']] = collection
    for saved in snapshot['folders']:
        collection = remap[saved['pointer']]
        _set_rna(collection, saved['rna'], remap)
        _set_properties(collection, saved['properties'], remap)
        from . import widget_collections
        parents = {_decode(item, remap) for item in saved['parents']}
        for parent in parents:
            if collection.name not in parent.children:
                parent.children.link(collection)
        for parent in widget_collections._parents(collection):
            if parent not in parents:
                parent.children.unlink(collection)
    for saved in snapshot['widgets']:
        mesh = saved['mesh'] if _pointer(saved['mesh']) == saved['mesh_pointer'] else None
        if mesh is None:
            if saved['mesh_name'] in bpy.data.meshes:
                raise ValueError(f"Cannot recover occupied widget mesh '{saved['mesh_name']}'.")
            mesh = saved['backup'].copy()
            mesh.name = saved['mesh_name']
        else:
            _restore_mesh_geometry(mesh, saved['backup'])
        remap[saved['mesh_pointer']] = mesh
        obj = saved['id'] if _pointer(saved['id']) == saved['pointer'] else None
        if obj is None:
            if saved['name'] in bpy.data.objects:
                raise ValueError(f"Cannot recover occupied widget '{saved['name']}'.")
            obj = bpy.data.objects.new(saved['name'], mesh)
        else:
            obj.data = mesh
        remap[saved['pointer']] = obj
    for saved in snapshot['widgets']:
        obj = remap[saved['pointer']]
        _set_properties(obj.data, saved['mesh_properties'], remap)
        _set_rna(obj, saved['rna'], remap)
        _set_properties(obj, saved['properties'], remap)
        parents = {_decode(value, remap) for value in saved['collections']}
        for collection in parents:
            if obj.name not in collection.objects:
                collection.objects.link(obj)
        for collection in tuple(obj.users_collection):
            if collection not in parents:
                collection.objects.unlink(obj)
    return remap


def restore(context, rig, snapshot):
    """Roll back a failed synchronous operation into the original armature IDs."""
    from . import limb_ik, bone_collections, control_colors, widget_collections
    if snapshot['closed']:
        raise ValueError('This Body transaction checkpoint has already been discarded.')
    if rig != snapshot['rig']:
        raise ValueError('The Body transaction belongs to a different armature.')
    assert_original_ids(snapshot)
    limb_ik._mode_set(context, rig, 'OBJECT')
    # Remove only resources born during this operation. Existing owned widget
    # meshes are retained unless their module explicitly deleted them.
    owners = _owners()
    for obj in tuple(bpy.data.objects):
        if obj.as_pointer() not in snapshot['objects_before'] and obj.get('character_designer_owner') in owners:
            mesh = obj.data if obj.type == 'MESH' else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh and not mesh.users and mesh.as_pointer() not in snapshot['meshes_before']:
                bpy.data.meshes.remove(mesh)
    while True:
        empty = [collection for collection in bpy.data.collections
                 if collection.as_pointer() not in snapshot['collections_before']
                 and collection.get('character_designer_owner') in owners
                 and not collection.objects and not collection.children]
        if not empty:
            break
        for collection in empty:
            bpy.data.collections.remove(collection)
    remap = _restore_resources(snapshot)
    # Disconnect new branches before Edit Mode invalidates their bones. This
    # avoids transient depsgraph references to just-deleted Foot/Root helpers.
    for name, state in snapshot['bones'].items():
        pb = rig.pose.bones.get(name)
        if pb is None:
            continue
        expected = {entry['name']: entry for entry in state['constraints']}
        for con in tuple(pb.constraints):
            entry = expected.get(con.name)
            if entry is None or con.type != entry['type']:
                pb.constraints.remove(con)
                continue
            for field in ('target', 'subtarget', 'pole_target', 'pole_subtarget', 'space_object', 'space_subtarget'):
                if field in entry['rna']:
                    setattr(con, field, _decode(entry['rna'][field], remap))
        if pb.custom_shape_transform and pb.custom_shape_transform.name not in snapshot['bones']:
            pb.custom_shape_transform = None
    for owner, saved in ((rig, snapshot['drivers']), (rig.data, snapshot['data_drivers'])):
        expected = {(entry['path'], entry['index']) for entry in saved}
        if owner.animation_data:
            for curve in tuple(owner.animation_data.drivers):
                if (curve.data_path, curve.array_index) not in expected:
                    owner.animation_data.drivers.remove(curve)
    rig.data.use_mirror_x = False
    # Native bones are never removed; only missing owned helpers are recreated.
    for name, state in snapshot['bones'].items():
        if state['native'] and name not in rig.data.bones:
            raise ValueError(f"Native bone '{name}' disappeared; an in-place rollback cannot replace it.")
    # Decide which Rest frames actually changed before entering Edit Mode.
    # EditBone reconstructs roll from the stored matrix; even a no-op align_roll
    # would introduce rounding into native calibration signatures.
    changed_rest = set()
    for name, state in snapshot['bones'].items():
        bone = rig.data.bones.get(name)
        if (bone is None or tuple(bone.head_local) != tuple(state['head'])
                or tuple(bone.tail_local) != tuple(state['tail'])
                or tuple(bone.matrix_local.to_3x3().col[2]) != tuple(state['z'])
                or (bone.parent.name if bone.parent else None) != state['parent']
                or bone.use_connect != state['connect']):
            changed_rest.add(name)
    limb_ik._mode_set(context, rig, 'EDIT')
    edit = rig.data.edit_bones
    for bone in tuple(edit):
        if bone.name not in snapshot['bones']:
            if bone.get('character_designer_owner') not in owners:
                raise ValueError('An unregistered bone was created during the Body transaction.')
            edit.remove(bone)
    for name in snapshot['bones']:
        if name not in edit:
            edit.new(name)
    for name in changed_rest:
        state = snapshot['bones'][name]
        bone = edit[name]
        bone.use_connect = False
    for name in changed_rest:
        state, bone = snapshot['bones'][name], edit[name]
        bone.parent = edit.get(state['parent']) if state['parent'] else None
        bone.head, bone.tail = state['head'], state['tail']
        bone.align_roll(Vector(state['z']))
    for name in changed_rest:
        edit[name].use_connect = snapshot['bones'][name]['connect']
    limb_ik._mode_set(context, rig, 'OBJECT')
    _set_rna(rig, snapshot['object'], remap)
    _set_rna(rig.data, snapshot['data_rna'], remap)
    _set_properties(rig, snapshot['properties'], remap)
    _set_properties(rig.data, snapshot['data_properties'], remap)
    for name, state in snapshot['bones'].items():
        bone, pb = rig.data.bones[name], rig.pose.bones[name]
        _set_rna(bone, state['bone'], remap)
        _set_properties(bone, state['bone_properties'], remap)
        _set_rna(pb, state['pose'], remap)
        _set_properties(pb, state['pose_properties'], remap)
        for key, value in state['pointers'].items():
            setattr(pb, key, _decode(value, remap))
        for key, value in state['bone_pointers'].items():
            setattr(bone, key, _decode(value, remap))
        pb.matrix_basis = Matrix(state['basis'])
        control_colors.restore_bone_state(pb, state['colors'])
        _constraints(pb, state['constraints'], remap)
    _constraints(rig, snapshot['constraints'], remap)
    _set_drivers(rig, snapshot['drivers'], remap)
    _set_drivers(rig.data, snapshot['data_drivers'], remap)
    if not snapshot['had_animation'] and rig.animation_data and not rig.animation_data.drivers and not rig.animation_data.action and not rig.animation_data.nla_tracks:
        rig.animation_data_clear()
    if not snapshot['data_had_animation'] and rig.data.animation_data and not rig.data.animation_data.drivers and not rig.data.animation_data.action and not rig.data.animation_data.nla_tracks:
        rig.data.animation_data_clear()
    bone_collections.restore_layout(rig, _decode(snapshot['layout'], remap))
    from . import forearm_twist
    for obj, raw in snapshot.get('corrective_records', ()):
        obj[forearm_twist.RECORD_KEY] = raw
    for saved in snapshot['folders']:
        widget_collections._restore_layers(remap[saved['pointer']], saved['layers'])
    for saved in snapshot['widgets']:
        obj = remap[saved['pointer']]
        for view, hidden in saved['hidden']:
            if obj.name in view.objects:
                obj.hide_set(hidden, view_layer=view)
    rig.data.update_tag()
    rig.update_tag(refresh={'OBJECT'})
    context.view_layer.update()
    limb_ik._restore_context(context, rig, snapshot['context'])
    assert_original_ids(snapshot)
    return {'restored': True, 'bones': len(snapshot['bones']), 'widgets': len(snapshot['widgets'])}


def discard(snapshot):
    """Release only temporary geometry backups, after successful commit/rollback."""
    if snapshot.get('closed'):
        return
    for mesh in snapshot.get('backups', ()):
        if _pointer(mesh) and not mesh.users:
            bpy.data.meshes.remove(mesh)
    snapshot['backups'] = []
    snapshot['closed'] = True
