"""Remove the validated Body graph as one unit; the caller owns rollback."""
import json
import bpy
from mathutils import Matrix, Vector

from . import (limb_ik, limb_ik_fk, foot_controls, torso_controls, eye_controls,
               spine_ik_fk, root_control, limb_fk_visuals, head_neck_visuals,
               body_detail_visuals, widget_collections, forearm_twist)

PRESERVED_REST_KEY = 'character_designer_preserved_body_rest_v1'
# A constrained matrix can contain minute shear that native location/rotation/
# scale channels cannot represent. Accept it only when BOTH the native skinning
# matrices and the evaluated bound surfaces stay inside these limits.
SKIN_MATRIX_TOLERANCE = 2e-4
SURFACE_TOLERANCE = 1e-4


def _error(message):
    return limb_ik.LimbIKError(message)


def _records(rig):
    result = {}
    for module in (torso_controls, eye_controls, spine_ik_fk, root_control,
                   limb_fk_visuals, head_neck_visuals, body_detail_visuals):
        if record := module.validate(rig):
            result[module.__name__.rsplit('.', 1)[-1]] = record
    for side, record in foot_controls.records(rig).items():
        result['foot_' + side] = record
    return result


def _rest_plan(rig, inventory):
    states = {}
    if limb_ik._is_direct_preroll_schema(inventory['schema']):
        registry = limb_ik._load_direct_rest_registry(rig, strict=True)
        for entry in registry.get('limbs', {}).values():
            for name, state in entry['original'].items():
                if name in states and states[name] != state:
                    raise _error('Direct Rest recovery disagrees about a native bone.')
                states[name] = state
    return states


def _correctives(rig, rest, *, keep_native_rest=False):
    result = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or forearm_twist.RECORD_KEY not in obj:
            continue
        records = forearm_twist._records(obj)
        matches = [record for record in records.values() if record.get('armature') == rig.name]
        if not matches:
            continue
        if keep_native_rest:
            # An older calibration can already be stale or disabled. Keeping
            # the native bind/rest does not require repairing that unrelated
            # profile, and must preserve its existing record and Key state.
            result.append((obj, records))
            continue
        for record in matches:
            if record['rest'] != forearm_twist._rest_signature(rig, record['chain']):
                raise _error(f"Forearm calibration on '{obj.name}' already has a different Rest frame; restore that setup first.")
            if record.get('topology') != forearm_twist._topology(obj.data):
                raise _error(f"Forearm calibration on '{obj.name}' has an older topology; keep the native Rest or resolve that calibration first.")
            for name in record['chain']:
                bone, original = rig.data.bones[name], rest.get(name)
                if original is not None and (
                        (Vector(original['head']) - bone.head_local).length > 1e-7
                        or (Vector(original['tail']) - bone.tail_local).length > 1e-7
                        or original['parent'] != (bone.parent.name if bone.parent else '')):
                    raise _error(f"Removing this rig would move the calibrated forearm's Rest axis on '{obj.name}'; preserve the calibration first.")
        result.append((obj, records))
    return result


def _surface(context, rig, obj):
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        to_rig = rig.matrix_world.inverted() @ evaluated.matrix_world
        return [to_rig @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def _bound_surfaces(context, rig):
    return [(obj, _surface(context, rig, obj)) for obj in bpy.data.objects
            if obj.type == 'MESH' and any(mod.type == 'ARMATURE' and mod.object == rig
                                         and mod.show_viewport for mod in obj.modifiers)]


def _check_surfaces(context, rig, surfaces):
    maximum = 0.
    for obj, before in surfaces:
        after = _surface(context, rig, obj)
        if len(before) != len(after):
            raise _error(f"The evaluated surface of '{obj.name}' changed topology during removal.")
        error = max(((a - b).length for a, b in zip(before, after)), default=0.)
        maximum = max(maximum, error)
        if error > SURFACE_TOLERANCE:
            raise _error(f"Removing controls would change '{obj.name}' ({error:.6g} rig units); the existing setup was kept.")
    return maximum


def preflight(context, rig, *, keep_native_rest=False):
    """Validate the complete deletion closure without changing mode or data."""
    if (rig is None or rig.type != 'ARMATURE' or rig.library or rig.data.library
            or rig.data.users != 1 or rig.mode == 'EDIT'):
        raise _error('Choose a local single-user armature outside Edit Mode.')
    if forearm_twist._SESSION is not None:
        raise _error('Finish the current Forearm calibration preview before removing Body controls.')
    inventory = limb_ik._validate_inventory(rig)
    records = _records(rig)
    resources = widget_collections._resources(rig)
    names = {bone.name for bone in inventory['bones']}
    constraints = {(pb.name, con.name) for pb, con, _record in inventory['records']}
    driver_paths = set(limb_ik_fk.owned_driver_paths(rig))
    driver_paths.update(root_control.owned_driver_paths(rig))
    for key, record in records.items():
        if key in {'limb_fk_visuals', 'head_neck_visuals', 'body_detail_visuals'}:
            continue
        names.update(record['bones'].values())
        constraints.update((entry['owner'], entry['name']) for entry in record['constraints'])
        driver_paths.update(entry['path'] for entry in record.get('drivers', ()))
    actual_owned = {bone.name for bone in rig.data.bones
                    if bone.get(limb_ik.OWNER_KEY) in limb_ik.GENERATED_CONTROL_OWNERS}
    if actual_owned != names:
        raise _error('A generated Body bone is missing from its ownership records; keep the existing rig intact.')
    if not names and not records:
        raise _error('This armature has no generated Body controls to remove.')
    if inventory['rigs']:
        limb_ik._removal_resources(context, rig, inventory)
    # Removing the whole graph makes internal Root/Spine/Foot dependencies safe.
    # Any authored animation still needs an explicit bake, never silent deletion.
    if names:
        for owner in (rig, rig.data):
            for action in limb_ik._actions_for_id(owner):
                if any(curve.data_path.startswith('pose.bones[') for curve in limb_ik._fcurves_for_action(action)):
                    raise _error('Body pose animation exists; bake or preserve those channels before removing the control rig.')
            for curve in owner.animation_data.drivers if owner.animation_data else ():
                if owner == rig and curve.data_path in driver_paths:
                    continue
                if curve.data_path.startswith('pose.bones['):
                    raise _error(f"An authored driver controls this body pose ({curve.data_path}); preserve it before removing controls.")
    widgets = {name for resource in resources for name in resource['objects']}
    bindings = {name: set() for name in widgets}
    for pb in rig.pose.bones:
        if pb.custom_shape and pb.custom_shape.name in widgets:
            bindings[pb.custom_shape.name].add(pb.name)
    source_bindings = set((inventory.get('source_widgets') or {}).get('bones', {}))
    for key in ('limb_fk_visuals', 'head_neck_visuals', 'body_detail_visuals'):
        if key in records:
            source_bindings.update(records[key]['bindings'])
    for bone in rig.data.bones:
        if bone.name not in names and bone.parent and bone.parent.name in names:
            raise _error(f"Native bone '{bone.name}' follows a generated controller; detach that artist dependency first.")
    for obj in bpy.data.objects:
        if obj.parent == rig and obj.parent_type == 'BONE' and obj.parent_bone in names:
            raise _error(f"Object '{obj.name}' follows a generated controller; preserve that attachment first.")
        for con in obj.constraints:
            if limb_ik._constraint_references_controls(con, rig, names):
                raise _error(f"An object constraint on '{obj.name}' follows generated controls.")
        if obj.type != 'ARMATURE':
            continue
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if obj == rig and (pb.name, con.name) in constraints:
                    continue
                if (obj == rig and pb.name in names) or limb_ik._constraint_references_controls(con, rig, names):
                    raise _error(f"An artist constraint on '{pb.name}' uses generated Body controls.")
            if pb.custom_shape and pb.custom_shape.name in widgets:
                if obj != rig or pb.name not in names | source_bindings:
                    raise _error('An owned Body widget is used by an unrelated bone.')
            if obj == rig and pb.name not in names and pb.custom_shape_transform and pb.custom_shape_transform.name in names:
                raise _error('A native display uses a generated control as its custom-shape frame.')
    for owner in foot_controls._driver_owners():
        for curve in owner.animation_data.drivers if getattr(owner, 'animation_data', None) else ():
            if owner == rig and curve.data_path in driver_paths:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id == rig and (target.bone_target in names or limb_ik._path_mentions_bone(target.data_path, names)):
                        raise _error('An external driver reads generated Body controls; preserve that dependency first.')
    for resource in resources:
        collection = resource['collection']
        if (collection is None or collection.children or collection.users > 1
                or set(collection.objects.keys()) != resource['objects']):
            raise _error('A Body widget collection contains artist data or external links.')
        for name in resource['objects']:
            obj = bpy.data.objects[name]
            if (obj.type != 'MESH' or obj.data.users != 1 or tuple(obj.users_collection) != (collection,)
                    or obj.users > 1 + len(bindings[name]) or obj.parent or obj.modifiers or obj.constraints
                    or obj.animation_data or obj.data.animation_data or obj.data.shape_keys):
                raise _error(f"Widget '{name}' has artist dependencies; preserve that use before removal.")
    for module in (limb_fk_visuals, head_neck_visuals, body_detail_visuals):
        key = module.__name__.rsplit('.', 1)[-1]
        if key in records:
            module._refuse_dependencies(rig, records[key])
    size_record = limb_fk_visuals._size_record(rig)
    if size_record:
        # Manual Auto Align legitimately changes the displayed anchor. These
        # inputs themselves are deleted, so no fitted-size restoration is needed.
        for name, entry in size_record['bindings'].items():
            pb = rig.pose.bones.get(name)
            if (name not in names or pb is None or pb.bone.get(limb_ik.OWNER_KEY) != limb_ik.OWNER_VALUE
                    or pb.custom_shape is None or pb.custom_shape.name != entry['object']):
                raise _error('An IK size recovery binding belongs to another controller; preserve it first.')
    rest = _rest_plan(rig, inventory)
    correctives = _correctives(rig, rest, keep_native_rest=keep_native_rest)
    rig.update_tag(refresh={'OBJECT'})
    context.view_layer.update()
    skin = {pb.name: pb.matrix @ pb.bone.matrix_local.inverted() for pb in rig.pose.bones if pb.name not in names}
    return {'rig': rig, 'records': records, 'inventory': inventory, 'resources': resources,
            'names': names, 'constraints': constraints, 'driver_paths': driver_paths,
            'rest': rest, 'skin': skin, 'correctives': correctives,
            'surfaces': _bound_surfaces(context, rig),
            'keep_native_rest': bool(keep_native_rest),
            'direct_rest_raw': rig.data.get(limb_ik.DIRECT_REST_KEY),
            'data_digest': limb_ik._armature_digest(rig)}


def _preserve_skin(context, rig, desired):
    ordered = sorted(desired, key=lambda name: len(rig.pose.bones[name].parent_recursive))
    matrices = {name: skin @ rig.data.bones[name].matrix_local for name, skin in desired.items()}
    for _attempt in range(4):
        for name in ordered:
            pb = rig.pose.bones[name]
            kwargs = ({'parent_matrix': pb.parent.matrix,
                       'parent_matrix_local': pb.parent.bone.matrix_local} if pb.parent else {})
            pb.matrix_basis = pb.bone.convert_local_to_pose(matrices[name], pb.bone.matrix_local, invert=True, **kwargs)
            rig.update_tag(refresh={'OBJECT'})
            context.view_layer.update()
        rig.update_tag(refresh={'OBJECT'})
        context.view_layer.update()
        error = max((abs((rig.pose.bones[name].matrix @ rig.data.bones[name].matrix_local.inverted())[i][j] - matrix[i][j])
                     for name, matrix in desired.items() for i in range(4) for j in range(4)), default=0.)
        if error < 3e-5:
            return error
    worst = max(desired, key=lambda name: max(abs((rig.pose.bones[name].matrix @ rig.data.bones[name].matrix_local.inverted())[i][j] - desired[name][i][j]) for i in range(4) for j in range(4)))
    pb = rig.pose.bones[worst]
    kwargs = ({'parent_matrix': pb.parent.matrix, 'parent_matrix_local': pb.parent.bone.matrix_local} if pb.parent else {})
    wanted_basis = pb.bone.convert_local_to_pose(matrices[worst], pb.bone.matrix_local, invert=True, **kwargs)
    decomposition = Matrix.LocRotScale(*wanted_basis.decompose())
    residual = max(abs(wanted_basis[i][j] - decomposition[i][j]) for i in range(4) for j in range(4))
    # The residual is a representation limit, not a failed solver iteration.
    # A separate evaluated-surface check below is mandatory before committing.
    if error <= SKIN_MATRIX_TOLERANCE and residual <= SKIN_MATRIX_TOLERANCE:
        return error
    raise _error(f'Native deformation could not be retained after removing controls ({worst}, error {error:.6g}).')


def execute(context, rig, plan):
    """Delete only the preflighted closure. Caller must roll back any exception."""
    from . import control_colors
    if plan['rig'] != rig or limb_ik._armature_digest(rig) != plan['data_digest']:
        raise _error('The character changed after removal preflight; inspect the current rig again.')
    removed = []
    for module in (body_detail_visuals, head_neck_visuals, limb_fk_visuals):
        key = module.__name__.rsplit('.', 1)[-1]
        if key in plan['records']:
            module.remove(context, rig)
            removed.append(key)
    registry = plan['inventory'].get('source_widgets')
    if registry:
        limb_ik._restore_source_widget_originals(rig, registry, strict_current=True)
    if rig.animation_data:
        for curve in tuple(rig.animation_data.drivers):
            if curve.data_path in plan['driver_paths']:
                rig.animation_data.drivers.remove(curve)
    for owner_name, constraint_name in plan['constraints']:
        pb = rig.pose.bones[owner_name]
        con = pb.constraints.get(constraint_name)
        if con is None:
            raise _error('A generated constraint disappeared after removal preflight.')
        pb.constraints.remove(con)
        if limb_ik.CONSTRAINT_REGISTRY_KEY in pb:
            own = limb_ik._constraint_registry(pb, strict=True)
            own.pop(constraint_name, None)
            limb_ik._write_constraint_registry(pb, own)
    error = 0.
    if plan['names']:
        mirror = rig.data.use_mirror_x
        rig.data.use_mirror_x = False
        try:
            limb_ik._mode_set(context, rig, 'EDIT')
            for name in plan['names']:
                rig.data.edit_bones.remove(rig.data.edit_bones[name])
            limb_ik._mode_set(context, rig, 'OBJECT')
            # Leave unchanged rest bones untouched (also preserves exact calibrated
            # signatures). Only actual Direct pre-roll changes need restoration.
            if not plan['keep_native_rest']:
                changed = {name: state for name, state in plan['rest'].items()
                           if not limb_ik._rest_state_matches(rig.data.bones[name], state)}
                limb_ik._restore_edit_rest_states(context, rig, changed)
        finally:
            rig.data.use_mirror_x = mirror
        error = _preserve_skin(context, rig, plan['skin'])
    for obj, records in (() if plan['keep_native_rest'] else plan['correctives']):
        changed = False
        for record in records.values():
            if record.get('armature') == rig.name:
                current = forearm_twist._rest_signature(rig, record['chain'])
                if current != record['rest']:
                    record['rest'], changed = current, True
        if changed:
            forearm_twist._write_records(obj, records)
    if plan['keep_native_rest'] and plan['direct_rest_raw'] and PRESERVED_REST_KEY not in rig.data:
        rig.data[PRESERVED_REST_KEY] = json.dumps({'version': 1, 'original_direct_rest': plan['direct_rest_raw'],
                                                'reason': 'Generated controls removed; current native bind/rest retained.'})
    surface_error = _check_surfaces(context, rig, plan['surfaces'])
    # Native shape assignments are already restored; purge intact owned leaves.
    for resource in plan['resources']:
        for name in resource['objects']:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                mesh = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if not mesh.users:
                    bpy.data.meshes.remove(mesh)
        collection = resource['collection']
        try:
            alive = collection.as_pointer() != 0
        except ReferenceError:
            alive = False
        if alive:
            bpy.data.collections.remove(collection)
    for collection in tuple(rig.data.collections_all):
        if (collection.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                and collection.get(limb_ik.ROLE_KEY) == 'CONTROL_COLLECTION'):
            if collection.bones:
                raise _error('An owned internal collection still contains bones after teardown.')
            rig.data.collections.remove(collection)
    for module in (foot_controls, torso_controls, eye_controls, spine_ik_fk, root_control):
        rig.data.pop(module.RECORD_KEY, None)
    for key in (limb_ik.ARMATURE_ID_KEY, limb_ik.SCHEMA_KEY, limb_ik.DIRECT_REST_KEY,
                limb_ik.SOURCE_WIDGETS_KEY, limb_ik.TARGET_ROTATION_VERSION_KEY,
                limb_fk_visuals.IK_SIZE_RECORD_KEY):
        rig.data.pop(key, None)
    widget_collections.prune_empty(context)
    control_colors.cleanup(rig)
    removed += [key for key in plan['records'] if key not in removed]
    if plan['inventory']['rigs']:
        removed.insert(0, 'limbs')
    return {'removed': removed, 'bones_removed': len(plan['names']), 'skin_error': error,
            'surface_error': surface_error,
            'correctives_preserved': len(plan['correctives']), 'retained_rest': plan['keep_native_rest'],
            'retained_rest_reason': 'Current native bind/rest and existing calibrations retained.' if plan['keep_native_rest'] else ''}
