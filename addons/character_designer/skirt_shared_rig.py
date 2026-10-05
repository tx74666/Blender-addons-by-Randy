"""Transactional migration of a verified skirt bone subset into its character.

The shared character object is never given skirt ownership. Only the imported
bones, their Dress collection and the source-owned helpers belong to the skirt.
The original armature remains alive until validation has completed.
"""
import json
import math

import bpy
from mathutils import Matrix, Vector

from . import skirt_rig as skirt


def _difference(a, b):
    return max(abs(x - y) for left, right in zip(a, b) for x, y in zip(left, right))


def _rigid(matrix):
    """Accept positive uniform similarity; reject shear or reflected axes."""
    linear = matrix.to_3x3()
    lengths = [linear.col[axis].length for axis in range(3)]
    if not skirt._valid_matrix(matrix) or linear.determinant() <= 0 or min(lengths) <= 1e-8:
        return False
    scale = sum(lengths) / 3.0
    rotation = linear * (1.0 / scale)
    return (max(abs(length / scale - 1.0) for length in lengths) < 2e-5
            and _difference(rotation.transposed() @ rotation, Matrix.Identity(3)) < 2e-5)


def _mesh_signature(obj):
    """Protected artist inputs, independent of the evaluated solver output."""
    data, keys = obj.data, obj.data.shape_keys
    return (tuple(tuple(vertex.co) for vertex in data.vertices),
            tuple(tuple(edge.vertices) for edge in data.edges),
            tuple(tuple(polygon.vertices) for polygon in data.polygons),
            tuple((uv.name, tuple(tuple(loop.uv) for loop in uv.data)) for uv in data.uv_layers),
            tuple((group.name, group.index, group.lock_weight) for group in obj.vertex_groups),
            tuple(tuple((weight.group, weight.weight) for weight in vertex.groups) for vertex in data.vertices),
            tuple((key.name, key.relative_key.name, key.value, key.mute,
                   tuple(tuple(point.co) for point in key.data)) for key in keys.key_blocks) if keys else ())


def _extent(vertices):
    return max((max(vertex[axis] for vertex in vertices) - min(vertex[axis] for vertex in vertices)
                for axis in range(3)), default=0.0) if vertices else 0.0


def _bone_frame(rig, name):
    pose = rig.pose.bones[name]
    matrix = rig.matrix_world @ pose.matrix
    return (rig.matrix_world @ pose.head, rig.matrix_world @ pose.tail,
            tuple(matrix.to_3x3().col[axis].normalized() for axis in range(3)),
            tuple(matrix.to_3x3().col[axis].length for axis in range(3)))


def _frame_error(left, right, unit_scale=1.0):
    position = max((left[0] - right[0]).length, (left[1] - right[1]).length)
    angle = max(math.atan2(a.cross(b).length, max(-1.0, min(1.0, a.dot(b))))
                for a, b in zip(left[2], right[2]))
    scale = max((abs(b * unit_scale / a - 1.0) for a, b in zip(left[3], right[3]) if a > 1e-8), default=math.inf)
    return position, angle, scale


def _state(obj):
    return (obj.parent, obj.parent_type, obj.parent_bone, obj.matrix_parent_inverse.copy(),
            obj.matrix_basis.copy())


def _restore_object(obj, state):
    obj.parent, obj.parent_type, obj.parent_bone = state[:3]
    obj.matrix_parent_inverse, obj.matrix_basis = state[3:]


def _properties(source, target, *, skip=(), replace=None):
    """Copy writable scalar/array/ID properties without copying derived matrices."""
    for prop in source.bl_rna.properties:
        name = prop.identifier
        if name in skip or name == 'rna_type' or prop.is_readonly or name not in target.bl_rna.properties:
            continue
        if prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM', 'POINTER'}:
            continue
        value = getattr(source, name)
        if getattr(prop, 'is_array', False):
            value = tuple(value)
        if replace and prop.type == 'POINTER':
            value = replace(value)
        try:
            setattr(target, name, value)
        except (AttributeError, TypeError) as exc:
            # Read-only derived pointer fields differ between Bone/EditBone.
            if prop.type != 'POINTER':
                raise skirt.SkirtRigError(f'Cannot preserve skirt property {name}.') from exc


def _raw_props(source, target, *, skip=()):
    for key in source.keys():
        if key not in skip:
            value = source[key]
            target[key] = value.to_dict() if hasattr(value, 'to_dict') else value


def _color(source, target):
    target.palette = source.palette
    if source.palette == 'CUSTOM':
        target.custom.normal = source.custom.normal
        target.custom.select = source.custom.select
        target.custom.active = source.custom.active


def _body_channels(rig):
    return {pb.name: (pb.rotation_mode, tuple(pb.location), tuple(pb.rotation_euler),
                      tuple(pb.rotation_quaternion), tuple(pb.rotation_axis_angle), tuple(pb.scale))
            for pb in rig.pose.bones}


def _restore_body_channels(rig, state):
    for name, values in state.items():
        pb = rig.pose.bones.get(name)
        if pb is None:
            continue
        pb.rotation_mode, pb.location, pb.rotation_euler, pb.rotation_quaternion, pb.rotation_axis_angle, pb.scale = values


def _mesh_world(obj, graph):
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        return tuple(tuple(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()


def _weighted_groups(mesh, names):
    indices = {group.index for group in mesh.vertex_groups if group.name in names}
    return any(g.group in indices and g.weight > 0 for vertex in mesh.data.vertices for g in vertex.groups)


def _driver_ids():
    """Include embedded material/world node trees, not only Object drivers."""
    seen = set()
    for prop in bpy.data.bl_rna.properties:
        if prop.type != 'COLLECTION':
            continue
        for value in getattr(bpy.data, prop.identifier):
            if not isinstance(value, bpy.types.ID):
                continue
            for item in (value, getattr(value, 'node_tree', None)):
                if item is not None and item.as_pointer() not in seen:
                    seen.add(item.as_pointer())
                    yield item


def _constraint_targets(constraint):
    for prop in constraint.bl_rna.properties:
        if prop.type == 'POINTER':
            value = getattr(constraint, prop.identifier)
            if isinstance(value, bpy.types.Object):
                yield value
    for target in getattr(constraint, 'targets', ()):
        if target.target:
            yield target.target


def _id_refs(value):
    if isinstance(value, bpy.types.ID):
        yield value
    elif hasattr(value, 'items'):
        for key, child in value.items():
            for path, reference in _id_ref_paths(child, (key,)):
                yield reference


def _id_ref_paths(value, prefix=()):
    if isinstance(value, bpy.types.ID):
        yield prefix, value
    elif hasattr(value, 'items'):
        for key, child in value.items():
            yield from _id_ref_paths(child, prefix + (key,))


def _scene_object_refs(value, prefix=(), seen=None):
    """Scene membership is legitimate; unrelated settings pointers are not."""
    seen = set() if seen is None else seen
    identity = value.as_pointer()
    if identity in seen:
        return
    seen.add(identity)
    for prop in value.bl_rna.properties:
        if prop.identifier == 'rna_type' or prop.type not in {'POINTER', 'COLLECTION'}:
            continue
        try:
            child = getattr(value, prop.identifier, None)
        except (AttributeError, ReferenceError):
            continue
        if callable(child):
            # Old add-on schemas may call a collection 'items' or 'values';
            # Python then resolves the ID-property helper method instead.
            child = value.get(prop.identifier) if hasattr(value, 'get') else None
        path = prefix + (prop.identifier,)
        if isinstance(child, bpy.types.Object):
            yield path, child
        elif isinstance(child, bpy.types.PropertyGroup):
            yield from _scene_object_refs(child, path, seen)
        elif prop.type == 'COLLECTION' and isinstance(child, type(bpy.data.objects)):
            for index, item in enumerate(child):
                if isinstance(item, bpy.types.PropertyGroup):
                    yield from _scene_object_refs(item, path + (str(index),), seen)


def preflight(context, source, armature=None, parent_bone=''):
    skirt._require_controls_for_setup(source, armature)
    record = skirt.read_record(source)
    if not record:
        raise skirt.SkirtRigError('Create the Dress setup before choosing Use Main Rig.')
    if skirt.is_shared(record):
        return {'source': source, 'main': source[skirt.RIG_KEY], 'record': record, 'already_shared': True}
    from . import skirt_original_mode
    if skirt_original_mode.CORRECTIONS in source:
        raise skirt.SkirtRigError('Clear the Dress pose correction before moving a separate Dress into the Main Rig.')
    old = source[skirt.RIG_KEY]
    armature = armature or (old.parent if old.parent and old.parent.type == 'ARMATURE' else None)
    if armature is None:
        raise skirt.SkirtRigError('Choose the Main Rig before moving Dress bones into it.')
    skirt._require_controls_for_setup(source, armature)
    anchor = skirt._anchor_bone(armature, parent_bone or (old.parent_bone if old.parent == armature else ''))
    if (armature == old or armature.type != 'ARMATURE' or armature.get(skirt.OWNER_KEY)
            or any(obj.library or obj.override_library or obj.data.library or obj.data.users != 1
                   for obj in (armature, old, source))):
        raise skirt.SkirtRigError('Use local, unshared character and skirt data for this migration.')
    if context.mode not in {'OBJECT', 'POSE'} or any(obj.mode == 'EDIT' for obj in (source, old, armature)):
        raise skirt.SkirtRigError('Finish Edit Mode or Edit Weights before moving Dress bones.')
    if any(context.view_layer.objects.get(obj.name) != obj for obj in (source, old, armature)):
        raise skirt.SkirtRigError('Include the Dress and both rigs in this view layer before migration.')
    if old.constraints or record.get('physics'):
        raise skirt.SkirtRigError('Keep this separate setup: active skirt physics or object constraints cannot be migrated safely yet.')
    if old.parent != armature or old.parent_type != 'BONE' or old.parent_bone != anchor:
        raise skirt.SkirtRigError('Attach this Dress to the chosen Main Rig and Hips bone before migration.')
    if old.animation_data and (old.animation_data.action or old.animation_data.nla_tracks):
        raise skirt.SkirtRigError('Keep this separate setup: its independent Dress animation needs a dedicated animation-space migration.')
    if source.animation_data and (source.animation_data.action or source.animation_data.nla_tracks or source.animation_data.drivers):
        raise skirt.SkirtRigError('Keep this separate setup: animated Dress object transforms cannot be migrated safely yet.')
    names = set.union(*skirt._bone_collection_layout(record))
    if names != set(old.data.bones.keys()) or names & set(armature.data.bones.keys()):
        raise skirt.SkirtRigError('Dress bone names are incomplete or conflict with the Main Rig; keep the current setup.')
    skins = [modifier for modifier in source.modifiers if modifier.type == 'ARMATURE' and modifier.object == old]
    if (len(skins) != 1 or not skins[0].use_vertex_groups or not skins[0].show_viewport or not skins[0].show_render
            or any(modifier.type == 'ARMATURE' and modifier.object == armature for modifier in source.modifiers)):
        raise skirt.SkirtRigError('Dress migration supports one effective group skin in viewport and render; preserve disabled or duplicate rig bindings first.')
    if any(modifier.type == 'ARMATURE' and modifier.object == old and modifier.use_bone_envelopes
           for modifier in source.modifiers):
        raise skirt.SkirtRigError('Dress uses bone envelopes; keep its separate rig so Body envelopes cannot change its binding.')
    if _weighted_groups(source, {bone.name for bone in armature.data.bones if bone.use_deform}):
        raise skirt.SkirtRigError('Dress has painted groups matching Body bones; preserve that separate binding before migration.')
    for obj in bpy.data.objects:
        if obj != source and obj.type == 'MESH' and any(m.type == 'ARMATURE' and m.object == armature for m in obj.modifiers):
            if any(m.type == 'ARMATURE' and m.object == armature and m.use_bone_envelopes for m in obj.modifiers):
                raise skirt.SkirtRigError('Another character mesh uses bone envelopes; keep Dress separate so its envelopes cannot change that binding.')
            if _weighted_groups(obj, names):
                raise skirt.SkirtRigError('Another character mesh has weights matching these Dress bones; keep the separate rig.')
    for bone in old.data.bones:
        if bone.bbone_segments != 1 or bone.bbone_custom_handle_start or bone.bbone_custom_handle_end:
            raise skirt.SkirtRigError('Custom bendy Dress bones need a dedicated migration; keep their separate rig.')
    drivers = list(old.animation_data.drivers) if old.animation_data else []
    existing_paths = {curve.data_path for curve in armature.animation_data.drivers} if armature.animation_data else set()
    if existing_paths & {curve.data_path for curve in drivers}:
        raise skirt.SkirtRigError('The Main Rig has an existing artist driver at a Dress path; preserve it before migration.')
    for curve in drivers:
        driver = curve.driver
        if (not curve.data_path.startswith('pose.bones[') or not curve.data_path.endswith('.influence')
                or driver.type != 'AVERAGE' or len(driver.variables) != 1
                or driver.variables[0].type != 'SINGLE_PROP'
                or driver.variables[0].targets[0].id != old
                or driver.variables[0].targets[0].data_path != '["physics_influence"]'):
            raise skirt.SkirtRigError('An artist Dress driver needs a dedicated migration; keep its separate rig.')
    owned = {obj for obj in bpy.data.objects if obj.get(skirt.OWNER_KEY) == record['owner']}
    permitted = owned | {source}
    containing_scenes = {scene for scene in bpy.data.scenes if scene.objects.get(old.name) == old}
    for scene in containing_scenes:
        for path, reference in _scene_object_refs(scene):
            if reference == old:
                raise skirt.SkirtRigError('An artist Scene setting still uses the separate Dress rig: '
                                         + scene.name + '.' + '.'.join(path))
    # Only the palette's validated recovery pointer may follow this migration.
    # Other artist references on the same Main Rig are still rejected below.
    from . import bone_color_palette as palette
    palette_paths = set()
    scheme = palette._read(armature)
    if scheme and old.get(palette.MAIN_KEY) == armature:
        palette_paths = {(palette.REFS_KEY, key) for key, rig in palette._refs(armature).items()
                         if rig == old}
        if palette_paths:
            for pb in old.pose.bones:
                if palette.BACKUP_KEY in pb and palette._backup(pb)['owner'] != scheme['id']:
                    raise skirt.SkirtRigError('Dress palette recovery belongs to another character.')
    known_users = permitted | {old} | set(old.users_collection) | containing_scenes
    if palette_paths:
        known_users.add(armature)
    unexpected_users = set(bpy.data.user_map(subset={old}).get(old, ())) - known_users
    if unexpected_users:
        raise skirt.SkirtRigError('An external data block still uses the separate Dress rig; preserve its reference first: '
                                 + ', '.join(sorted(item.name for item in unexpected_users)))
    children, modifiers = [], []
    for obj in bpy.data.objects:
        if obj.parent == old:
            if obj not in permitted or obj.parent_type != 'OBJECT':
                raise skirt.SkirtRigError('A non-owned or bone-parented child still depends on the separate Dress rig.')
            children.append((obj, _state(obj)))
        for modifier in obj.modifiers:
            if getattr(modifier, 'object', None) == old:
                if obj not in permitted or modifier.type not in {'ARMATURE', 'HOOK'}:
                    raise skirt.SkirtRigError('An external modifier depends on the separate Dress rig; preserve that setup first.')
                modifiers.append((modifier, (modifier.matrix_inverse.copy(), tuple(modifier.center))
                                  if modifier.type == 'HOOK' else None))
        constraints = list(obj.constraints)
        if obj.type == 'ARMATURE':
            constraints.extend(con for pb in obj.pose.bones for con in pb.constraints)
        for con in constraints:
            if old in set(_constraint_targets(con)) and obj != old:
                raise skirt.SkirtRigError('An external constraint depends on the separate Dress rig.')
        if obj.animation_data and obj != old:
            if any(target.id == old for curve in obj.animation_data.drivers
                   for variable in curve.driver.variables for target in variable.targets):
                raise skirt.SkirtRigError('An external driver depends on the separate Dress rig.')
    for item in _driver_ids():
        if item == old:
            continue
        animation = getattr(item, 'animation_data', None)
        if animation and any(target.id == old for curve in animation.drivers
                             for variable in curve.driver.variables for target in variable.targets):
            raise skirt.SkirtRigError('An external data or node driver depends on the separate Dress rig.')
        for path, reference in _id_ref_paths(dict(item.items())):
            allowed = ((item == source and path == (skirt.RIG_KEY,))
                       or (item == armature and path in palette_paths))
            if reference == old and not allowed:
                raise skirt.SkirtRigError('An artist data reference still uses the separate Dress rig; preserve that reference first.')
    for bone in old.data.bones:
        for item in (bone, old.pose.bones[bone.name]):
            if old in set(_id_refs(item)):
                raise skirt.SkirtRigError('An artist Dress bone reference uses its separate rig; preserve that reference first.')
    for keys in bpy.data.shape_keys:
        if keys.animation_data and any(target.id == old for curve in keys.animation_data.drivers
                                       for variable in curve.driver.variables for target in variable.targets):
            raise skirt.SkirtRigError('A Shape Key driver depends on the separate Dress rig; preserve that driver first.')
    for name in record['cage']:
        curve = bpy.data.objects.get(name)
        if curve is None or curve.type != 'CURVE' or curve.get(skirt.OWNER_KEY) != record['owner']:
            raise skirt.SkirtRigError('Restore the complete owned Dress wire before migration.')
        if any(spline.type == 'BEZIER' for spline in curve.data.splines):
            raise skirt.SkirtRigError('Edited Bezier Dress wires need a dedicated migration.')
        count = sum(len(spline.points) for spline in curve.data.splines)
        covered = set()
        for modifier in curve.modifiers:
            if (modifier.type != 'HOOK' or modifier.object != old or modifier.subtarget not in names
                    or modifier.strength != 1.0 or modifier.falloff_type != 'NONE'):
                raise skirt.SkirtRigError('The Dress wire has edited hooks; preserve its separate rig.')
            covered.update(modifier.vertex_indices)
        if covered != set(range(count)):
            raise skirt.SkirtRigError('Every Dress wire point must have its original bone Hook before migration.')
    for pb in old.pose.bones:
        for con in pb.constraints:
            if con.type not in {'SPLINE_IK', 'COPY_TRANSFORMS', 'COPY_ROTATION'}:
                raise skirt.SkirtRigError('A custom Dress constraint needs a dedicated space migration.')
            if getattr(con, 'target', None) == old and (con.owner_space != 'LOCAL' or con.target_space != 'LOCAL'):
                raise skirt.SkirtRigError('A non-local Dress constraint cannot preserve its space during this migration.')
    context.view_layer.update()
    transform = (armature.data.bones[anchor].matrix_local @ armature.pose.bones[anchor].matrix.inverted()
                 @ armature.matrix_world.inverted() @ old.matrix_world)
    if not _rigid(transform):
        raise skirt.SkirtRigError('Dress uses nonuniform scale, shear or reflected axes; keep its separate rig until its space can be preserved.')
    unit_scale = sum(transform.to_3x3().col[axis].length for axis in range(3)) / 3.0
    if abs(unit_scale - 1.0) < 1e-6:
        unit_scale = 1.0
    graph = context.evaluated_depsgraph_get()
    geometry = {obj: _mesh_world(obj, graph) for obj in permitted if obj.type in {'MESH', 'CURVE'}
                and (obj == source or obj in {bpy.data.objects.get(name) for name in record['cage']})}
    return {'source': source, 'old': old, 'main': armature, 'anchor': anchor, 'record': record,
            'names': names, 'transform': transform, 'unit_scale': unit_scale,
            'drivers': drivers, 'children': children,
            'modifiers': modifiers, 'geometry': geometry, 'body_channels': _body_channels(armature),
            'protected_mesh': {obj: _mesh_signature(obj) for obj in permitted if obj.type == 'MESH'},
            'old_frames': {name: _bone_frame(old, name) for name in names},
            'old_channels': _body_channels(old), 'extent': max(_extent(geometry[source]), 1e-6),
            'body_rest': {b.name: (b.parent.name if b.parent else '', b.matrix_local.copy()) for b in armature.data.bones},
            'main_basis': armature.matrix_basis.copy(), 'old_basis': old.matrix_basis.copy(),
            'frame': context.scene.frame_current, 'subframe': context.scene.frame_subframe}


def _copy_bones(context, plan):
    main, old, transform = plan['main'], plan['old'], plan['transform']
    unit_scale = plan['unit_scale']
    skirt._activate(context, main, 'EDIT')
    for bone in old.data.bones:
        new = main.data.edit_bones.new(bone.name)
        new.head, new.tail = transform @ bone.head_local, transform @ bone.tail_local
        new.align_roll(transform.to_3x3() @ bone.matrix_local.to_3x3().col[2])
        _properties(bone, new, skip={'name', 'head', 'tail', 'matrix', 'parent', 'use_connect',
                                    'bbone_custom_handle_start', 'bbone_custom_handle_end'})
        for name in ('envelope_distance', 'head_radius', 'tail_radius', 'bbone_x', 'bbone_z'):
            setattr(new, name, getattr(bone, name) * unit_scale)
        _raw_props(bone, new)
        new[skirt.OWNER_KEY] = plan['record']['owner']
        new[skirt.SOURCE_KEY] = plan['source']
    for bone in old.data.bones:
        new = main.data.edit_bones[bone.name]
        new.parent = main.data.edit_bones[bone.parent.name if bone.parent else plan['anchor']]
        new.use_connect = bone.use_connect if bone.parent else False
    bpy.ops.object.mode_set(mode='OBJECT')
    group = main.data.collections.new(skirt.BONE_COLLECTION_NAME)
    group[skirt.OWNER_KEY] = plan['record']['owner']
    plan['collection'] = group
    plan['created_drivers'] = []
    for bone in old.data.bones:
        new = main.data.bones[bone.name]
        for previous in tuple(new.collections):
            previous.unassign(new)
        group.assign(new)
        new.hide, new.hide_select = bone.hide, bone.hide_select
        _color(bone.color, new.color)
        pb, copied = old.pose.bones[bone.name], main.pose.bones[bone.name]
        copied.rotation_mode = pb.rotation_mode
        _properties(pb, copied, skip={'name', 'matrix', 'matrix_basis', 'matrix_channel', 'bone', 'parent',
                                     'head', 'tail', 'custom_shape_transform', 'color', 'rotation_mode'})
        if unit_scale != 1.0:
            # Object scale becomes rest-bone lengths. Local translations and
            # unscaled custom-shape coordinates therefore change unit space.
            copied.location = pb.location * unit_scale
            copied.custom_shape_translation = pb.custom_shape_translation * unit_scale
            if not pb.use_custom_shape_bone_size:
                copied.custom_shape_scale_xyz = pb.custom_shape_scale_xyz * unit_scale
        _raw_props(pb, copied)
        if pb.custom_shape_transform:
            copied.custom_shape_transform = main.pose.bones[pb.custom_shape_transform.name]
        _color(pb.color, copied.color)
        for constraint in pb.constraints:
            new_constraint = copied.constraints.new(constraint.type)
            _properties(constraint, new_constraint, replace=lambda value: main if value == old else value)
    waist = main.pose.bones[plan['record']['controls']['waist']]
    waist['physics_influence'] = old.get('physics_influence', 0.0)
    waist.id_properties_ui('physics_influence').update(min=0.0, max=1.0,
                                                      description='Add simulated motion to this Dress')
    path = waist.path_from_id() + '["physics_influence"]'
    for original in plan['drivers']:
        curve = main.driver_add(original.data_path)
        plan['created_drivers'].append(original.data_path)
        curve.mute = original.mute
        driver = curve.driver
        driver.type = original.driver.type
        driver.expression = original.driver.expression
        for variable in tuple(driver.variables):
            driver.variables.remove(variable)
        for old_variable in original.driver.variables:
            variable = driver.variables.new()
            variable.name, variable.type = old_variable.name, old_variable.type
            variable.targets[0].id = main
            variable.targets[0].data_path = path
        curve.extrapolation = original.extrapolation
        for modifier in tuple(curve.modifiers):
            curve.modifiers.remove(modifier)
        for modifier in original.modifiers:
            copied_modifier = curve.modifiers.new(modifier.type)
            _properties(modifier, copied_modifier)
        if original.keyframe_points:
            curve.keyframe_points.add(len(original.keyframe_points))
            for before, after in zip(original.keyframe_points, curve.keyframe_points):
                _properties(before, after)
            curve.update()


def _remove_bones(context, main, names):
    skirt._activate(context, main, 'EDIT')
    for name in names:
        bone = main.data.edit_bones.get(name)
        if bone is not None:
            main.data.edit_bones.remove(bone)
    bpy.ops.object.mode_set(mode='OBJECT')


def _validate_result(context, plan):
    main, old = plan['main'], plan['old']
    extent = plan['extent']
    # Native Spline IK can amplify sub-micrometre curve rounding into a small
    # angular difference. Bounds were measured against unchanged controls,
    # re-evaluated legacy rigs and the real Cosha; raw artist data stays exact.
    curve_limit = extent * 2e-6
    control_position_limit, control_angle_limit = extent * 3e-6, 1e-5
    # Verified worst cases: Cosha 2.93e-4 of extent / 7.75e-4 rad;
    # four-wire three-segment fixture 4.36e-4 / 6.89e-4 rad. The
    # tighter control/wire limits distinguish rounding from a space error.
    solver_position_limit, solver_angle_limit = extent * 5e-4, 1.2e-3
    solver_scale_limit = 7e-4
    controls, _deform, _mechanism = skirt._bone_collection_layout(plan['record'])
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    for obj, expected in plan['geometry'].items():
        actual = _mesh_world(obj, graph)
        error = max(((Vector(left) - Vector(right)).length for left, right in zip(actual, expected)), default=0)
        limit = curve_limit if obj.type == 'CURVE' else solver_position_limit
        if len(actual) != len(expected) or error > limit:
            raise skirt.SkirtRigError(f'Dress migration changed {obj.name} geometry ({error:.8f}); the setup was restored.')
    for obj, expected in plan['protected_mesh'].items():
        if _mesh_signature(obj) != expected:
            raise skirt.SkirtRigError('An artist mesh, Shape Key, UV or weight changed; Dress migration was restored.')
    imported_channels = _body_channels(main)
    for name, expected in plan['old_channels'].items():
        expected = list(expected)
        expected[1] = tuple(Vector(expected[1]) * plan['unit_scale'])
        if imported_channels[name] != tuple(expected):
            raise skirt.SkirtRigError('A Dress control channel changed outside its coordinate units; setup restored.')

    def check_frames(current=False):
        for name in plan['names']:
            expected = plan['old_frames'][name] if current else _bone_frame(old, name)
            position, angle, scale = _frame_error(expected, _bone_frame(main, name), plan['unit_scale'])
            position_limit = control_position_limit if name in controls else solver_position_limit
            angle_limit = control_angle_limit if name in controls else solver_angle_limit
            scale_limit = 1e-5 if name in controls else solver_scale_limit
            if position > position_limit or angle > angle_limit or scale > scale_limit:
                raise skirt.SkirtRigError('Dress bone space changed beyond native solver precision: '
                                         f'{name}, position {position:.8f}, angle {angle:.8f}, scale {scale:.8f}. Setup restored.')

    check_frames(current=True)
    frames = {plan['frame'], context.scene.frame_start, context.scene.frame_end,
              max(context.scene.frame_start, plan['frame'] - 1), min(context.scene.frame_end, plan['frame'] + 1)}
    try:
        for frame in sorted(frames):
            context.scene.frame_set(frame, subframe=plan['subframe'] if frame == plan['frame'] else 0.0)
            if frame == plan['frame']:
                _restore_body_channels(main, plan['body_channels'])
                main.matrix_basis = plan['main_basis']
                old.matrix_basis = plan['old_basis']
            context.view_layer.update()
            check_frames()
    finally:
        context.scene.frame_set(plan['frame'], subframe=plan['subframe'])
        _restore_body_channels(main, plan['body_channels'])
        main.matrix_basis = plan['main_basis']
        old.matrix_basis = plan['old_basis']
        context.view_layer.update()
    channels = _body_channels(main)
    if {name: channels[name] for name in plan['body_channels']} != plan['body_channels']:
        raise skirt.SkirtRigError('A Body or Hair pose channel changed; Dress migration was restored.')
    for name, (parent, matrix) in plan['body_rest'].items():
        bone = main.data.bones[name]
        if (bone.parent.name if bone.parent else '') != parent or _difference(bone.matrix_local, matrix) > 1e-7:
            raise skirt.SkirtRigError('A Body rest bone changed during Dress migration; setup restored.')


def unify(context, source, armature=None, parent_bone=''):
    from . import bone_display
    plan = preflight(context, source, armature, parent_bone)
    if plan.get('already_shared'):
        return plan['record']
    main, old, record = plan['main'], plan['old'], plan['record']
    saved_context = skirt._context_state(context)
    display = bone_display._snapshot(main)
    active_bone = main.data.bones.active.name if main.data.bones.active else None
    selected_bones = {pb.name: pb.select for pb in main.pose.bones}
    active_collection = main.data.collections.active.name if main.data.collections.active else None
    raw = source[skirt.RECORD_KEY]
    registry = dict(main.get(skirt.SHARED_SOURCES_KEY, {}))
    created = False
    try:
        created = True
        _copy_bones(context, plan)
        for obj, state in plan['children']:
            obj.parent = main
            if obj.type == 'CURVE':
                # Preserve the legacy wire's effective Hips frame directly.
                # Hook evaluation cancels its object's frame, so this does not
                # add Hips motion twice. The skinned source, in contrast, must
                # use the neutral rest frame for its Armature modifier.
                obj.parent_type, obj.parent_bone = 'BONE', plan['anchor']
                obj.matrix_parent_inverse = old.matrix_parent_inverse @ old.matrix_basis @ state[3]
            else:
                obj.parent_type, obj.parent_bone = 'OBJECT', ''
                obj.matrix_parent_inverse = plan['transform'] @ state[3]
            obj.matrix_basis = state[4]
        for modifier, hook in plan['modifiers']:
            modifier.object = main
            if hook is not None:
                # Blender's Hook object setter recalculates its bind inverse.
                # EditBone rest axes are normalized: the similarity's scalar
                # moves into bone lengths, not the pose matrix's axes. Restore
                # the corresponding local Hook units without editing the wire.
                inverse = hook[0]
                if plan['unit_scale'] != 1.0:
                    scale = plan['unit_scale']
                    inverse = Matrix.Diagonal((scale, scale, scale, 1.0)) @ inverse
                modifier.matrix_inverse, modifier.center = inverse, hook[1]
        context.view_layer.update()
        _validate_result(context, plan)
        updated = json.loads(json.dumps(record))
        updated['rig'], updated['character'], updated['parent_bone'] = main.name, main.name, plan['anchor']
        updated['shared'] = {'anchor': plan['anchor'], 'names': sorted(plan['names']),
                             'collection': plan['collection'].name, 'physics_property': 'physics_influence',
                             'space_matrix': skirt._matrix_values(plan['transform'])}
        updated['owned_objects'] = [name for name in updated['owned_objects'] if name != old.name]
        main[skirt.SHARED_SOURCES_KEY] = dict(registry, **{record['owner']: source})
        source[skirt.RIG_KEY] = main
        skirt.write_record(source, updated)
        # Last fallible validation occurs while the old rig is still available.
        skirt.read_record(source)
        skirt.select_controls(context, source)
        from . import bone_color_palette
        bone_color_palette.migrate_rig_reference(main, old)
    except Exception as exc:
        source[skirt.RIG_KEY] = old
        source[skirt.RECORD_KEY] = raw
        if registry:
            main[skirt.SHARED_SOURCES_KEY] = registry
        else:
            main.pop(skirt.SHARED_SOURCES_KEY, None)
        for modifier, hook in plan['modifiers']:
            modifier.object = old
            if hook is not None:
                modifier.matrix_inverse, modifier.center = hook
        for obj, state in plan['children']:
            _restore_object(obj, state)
        for path in plan.get('created_drivers', ()):
            main.driver_remove(path)
        if created:
            _remove_bones(context, main, plan['names'])
        collection = plan.get('collection')
        if collection is not None:
            main.data.collections.remove(collection)
        _restore_body_channels(main, plan['body_channels'])
        main.matrix_basis = plan['main_basis']
        old.matrix_basis = plan['old_basis']
        bone_display._restore(main, display)
        for name, selected in selected_bones.items():
            main.pose.bones[name].select = selected
        main.data.bones.active = main.data.bones.get(active_bone) if active_bone else None
        if active_collection is not None:
            main.data.collections.active = main.data.collections_all.get(active_collection)
        skirt._restore_context(context, saved_context)
        context.view_layer.update()
        if isinstance(exc, skirt.SkirtRigError):
            raise
        raise skirt.SkirtRigError(f'Dress migration was rolled back: {exc}') from exc
    data = old.data
    bpy.data.objects.remove(old, do_unlink=True)
    if data.users == 0:
        bpy.data.armatures.remove(data)
    return updated


def remove(context, source, *, allow_animation=False):
    """Remove only this source's managed subset, never the character armature."""
    skirt._require_controls_for_setup(source)
    record = skirt.read_record(source)
    main = source[skirt.RIG_KEY]
    names = set(record['shared']['names'])
    surface, surface_plan = skirt._surface_remove_plan(context, source, main, record)
    allowed_dependencies = surface_plan['allowed_dependencies'] if surface_plan is not None else frozenset()
    if record.get('physics'):
        # Existing cache/object cleanup is still source-owned; protect a running bake.
        from . import skirt_physics
        _proxy, cloth = skirt_physics._cloth(record)
        if cloth.point_cache.is_baking:
            raise skirt.SkirtRigError('Finish the Dress physics bake before removing its setup.')
    if not allow_animation and main.animation_data:
        from . import limb_ik
        prefixes = tuple(main.pose.bones[name].path_from_id() for name in names)
        if any(curve.data_path.startswith(prefixes) for action in limb_ik._actions_for_id(main)
               for curve in limb_ik._fcurves_for_action(action)):
            raise skirt.SkirtRigError('This Dress subset has animation; explicitly keep or remove that animation first.')
    expected = source.modifiers.get(record['modifier'])
    if expected is None or expected.type != 'ARMATURE' or expected.object != main:
        raise skirt.SkirtRigError('Restore the shared Dress skin modifier before removing its setup.')
    if any(b.parent and b.parent.name in names and b.name not in names for b in main.data.bones):
        raise skirt.SkirtRigError('A non-Dress bone depends on this subset; preserve that bone before removal.')
    for obj in bpy.data.objects:
        if obj.parent == main and obj.parent_type == 'BONE' and obj.parent_bone in names:
            if obj.get(skirt.OWNER_KEY) != record['owner']:
                raise skirt.SkirtRigError('An artist object is attached to these Dress bones; preserve it before removal.')
        for modifier in obj.modifiers:
            if (getattr(modifier, 'object', None) == main and getattr(modifier, 'subtarget', '') in names
                    and obj.get(skirt.OWNER_KEY) != record['owner']):
                raise skirt.SkirtRigError('An artist Hook uses these Dress bones; preserve it before removal.')
        for constraint in obj.constraints:
            if any(target == main for target in _constraint_targets(constraint)):
                if (getattr(constraint, 'subtarget', '') in names or getattr(constraint, 'pole_subtarget', '') in names
                        or getattr(constraint, 'space_subtarget', '') in names
                        or any(target.target == main and target.subtarget in names for target in getattr(constraint, 'targets', ()))):
                    raise skirt.SkirtRigError('An artist object constraint uses these Dress bones; preserve it before removal.')
        if obj.type == 'ARMATURE':
            for pb in obj.pose.bones:
                if (obj != main or pb.name not in names) and pb.custom_shape and pb.custom_shape.get(skirt.OWNER_KEY) == record['owner']:
                    raise skirt.SkirtRigError('An artist bone uses a Dress shape; preserve that shape before removal.')
                if obj != main or pb.name not in names:
                    for con in pb.constraints:
                        if (obj, pb.name, con.name) in allowed_dependencies:
                            continue
                        if main in set(_constraint_targets(con)) and (
                                getattr(con, 'subtarget', '') in names or getattr(con, 'pole_subtarget', '') in names
                                or getattr(con, 'space_subtarget', '') in names
                                or any(target.target == main and target.subtarget in names for target in getattr(con, 'targets', ()))):
                            raise skirt.SkirtRigError('A non-Dress constraint uses these Dress bones; preserve it before removal.')
        if obj != source and obj.get(skirt.OWNER_KEY) != record['owner'] and obj.type == 'MESH':
            if any(mod.type == 'ARMATURE' and mod.object == main for mod in obj.modifiers) and _weighted_groups(obj, names):
                raise skirt.SkirtRigError('Another artist mesh is weighted to these Dress bones; preserve it before removal.')
    prefixes = tuple(main.pose.bones[name].path_from_id() for name in names)
    bone_prefixes = tuple(main.data.bones[name].path_from_id() for name in names)
    object_bone_prefixes = tuple('data.' + path for path in bone_prefixes)
    for item in _driver_ids():
        animation = getattr(item, 'animation_data', None)
        if not animation:
            continue
        for curve in animation.drivers:
            if item == main.data and curve.data_path.startswith(bone_prefixes):
                raise skirt.SkirtRigError('An artist bone-data driver uses this Dress subset; preserve it before removal.')
            # Drivers being removed with this exact subset are expected.
            if item == main and curve.data_path.startswith(prefixes):
                continue
            if isinstance(item, bpy.types.Object) and item.get(skirt.OWNER_KEY) == record['owner']:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if ((target.id == main and (target.bone_target in names
                                                or target.data_path.startswith(prefixes + object_bone_prefixes)))
                            or (target.id == main.data and target.data_path.startswith(bone_prefixes))):
                        raise skirt.SkirtRigError('An external artist driver uses these Dress bones; preserve it before removal.')
    paths = [curve.data_path for curve in main.animation_data.drivers
             if curve.data_path.startswith(tuple(main.pose.bones[name].path_from_id() for name in names))] if main.animation_data else []
    # All expected errors are checked before destructive cleanup. The caller's
    # Blender operator supplies Undo for a committed removal.
    skirt._activate(context, source)
    if surface is not None:
        surface.commit_remove(source, surface_plan)
    source.modifiers.remove(expected)
    for name in record['groups']:
        group = source.vertex_groups.get(name)
        if group:
            source.vertex_groups.remove(group)
    skirt._restore_parent(source, record)
    for path in paths:
        main.driver_remove(path)
    _remove_bones(context, main, names)
    group = main.data.collections_all.get(record['shared']['collection'])
    if group and group.get(skirt.OWNER_KEY) == record['owner']:
        main.data.collections.remove(group)
    registry = dict(main.get(skirt.SHARED_SOURCES_KEY, {}))
    registry.pop(record['owner'], None)
    if registry:
        main[skirt.SHARED_SOURCES_KEY] = registry
    else:
        main.pop(skirt.SHARED_SOURCES_KEY, None)
    skirt._purge_owned(source, record['owner'])
    skirt._clear_source_properties(source)
    source.vertex_groups.active_index = min(record['original']['active_group'], len(source.vertex_groups) - 1)
    skirt._activate(context, source)
    context.view_layer.update()
    return record
