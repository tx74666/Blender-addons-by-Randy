"""Optional, removable FK torso controls with a shared additive bend."""
from __future__ import annotations

import json
import math
import uuid

import bpy
from mathutils import Matrix, Vector

OWNER_KEY = "character_designer_owner"
OWNER_VALUE = "torso_controls"
ROLE_KEY = "character_designer_torso_role"
ID_KEY = "character_designer_torso_id"
RECORD_KEY = "character_designer_torso_controls_v1"
VERSION = 1


def _limb():
    from . import limb_ik
    return limb_ik


def _error(message):
    return _limb().LimbIKError(message)


def get_record(armature):
    raw = armature.data.get(RECORD_KEY)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        if record["version"] != VERSION or not isinstance(record["sources"], list):
            raise ValueError("unsupported version")
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("Torso Controls recovery data is invalid; restore a saved copy.") from exc


def collection_members(armature):
    record = get_record(armature)
    if record is None:
        return {"generated": set(), "replaced": set(), "always": set(), "visible": set()}
    visible = {record["bend"], *record["controls"].values()}
    return {"generated": set(record["bones"].values()), "replaced": set(record["sources"]),
            "always": visible, "visible": visible}


def validate(armature, inventory=None):
    """Validate only this optional module, without entering limb inventory again."""
    record = get_record(armature)
    if record is None:
        if any(b.get(OWNER_KEY) == OWNER_VALUE for b in armature.data.bones):
            raise _error('Torso Controls bones have lost their recovery data.')
        return None
    try:
        names = set(record['bones'].values())
        actual = {b.name for b in armature.data.bones if b.get(OWNER_KEY) == OWNER_VALUE}
        if actual != names or len(record['sources']) not in {3, 4}:
            raise _error('Torso Controls bones were added, removed, or renamed.')
        for role, name in record['bones'].items():
            bone = armature.data.bones[name]
            state = record['bone_states'][role]
            if not _owned(bone, record, role) or bone.use_deform or not _same_rest(bone, state):
                raise _error(f"Torso Controls bone '{name}' was structurally edited.")
        for name, state in record['source_states'].items():
            bone = armature.data.bones.get(name)
            if bone is None or not _same_rest(bone, state) or bone.use_deform != state['deform']:
                raise _error(f"Torso source '{name}' changed; restore its original structure first.")
        own_cons = {(e['owner'], e['name']) for e in record['constraints']}
        for pb in armature.pose.bones:
            if pb.name in names | set(record['sources']):
                if any((pb.name, c.name) not in own_cons for c in pb.constraints):
                    raise _error(f"Bone '{pb.name}' has additional constraints; preserve that setup first.")
        for entry in record['constraints']:
            con = armature.pose.bones[entry['owner']].constraints.get(entry['name'])
            if (con is None or con.type != entry['type'] or con.target != armature
                    or con.mute or any(not _same_value(getattr(con, k), v) for k, v in entry['fields'].items())):
                raise _error(f"Torso constraint '{entry['name']}' was edited.")
        for role, entry in record['widgets'].items():
            obj = bpy.data.objects.get(entry['object'])
            if (obj is None or not _owned(obj, record, 'WIDGET') or obj.type != 'MESH'
                    or obj.data.name != entry['mesh'] or not _owned(obj.data, record, 'WIDGET_MESH')
                    or armature.pose.bones[record['bones'][role]].custom_shape != obj):
                raise _error('A Torso Controls widget was replaced or removed.')
        collection = bpy.data.collections.get(record['widget_collection'])
        if collection is None or not _owned(collection, record, 'WIDGET_COLLECTION'):
            raise _error('The Torso Controls widget collection is missing.')
    except (KeyError, TypeError, AttributeError) as exc:
        raise _error('Torso Controls recovery data no longer matches this rig.') from exc
    return record


def _tag(item, record, role):
    item[OWNER_KEY], item[ID_KEY], item[ROLE_KEY] = OWNER_VALUE, record['id'], role


def _owned(item, record, role):
    return (item.get(OWNER_KEY) == OWNER_VALUE and item.get(ID_KEY) == record['id']
            and item.get(ROLE_KEY) == role)


def _same_value(a, b):
    return abs(a - b) < 1e-6 if isinstance(b, float) else a == b


def _state(bone):
    return {'head': list(bone.head_local), 'tail': list(bone.tail_local),
            'matrix': [list(row) for row in bone.matrix_local],
            'parent': bone.parent.name if bone.parent else '', 'deform': bone.use_deform,
            'connect': bone.use_connect, 'inherit_scale': bone.inherit_scale,
            'inherit_rotation': bone.use_inherit_rotation,
            'local_location': bone.use_local_location}


def _same_rest(bone, state):
    return ((bone.head_local - Vector(state['head'])).length < 1e-5
            and (bone.tail_local - Vector(state['tail'])).length < 1e-5
            and max(abs(bone.matrix_local[i][j] - state['matrix'][i][j]) for i in range(4) for j in range(4)) < 1e-5
            and (bone.parent.name if bone.parent else '') == state['parent']
            and bone.use_connect == state['connect'] and bone.inherit_scale == state['inherit_scale']
            and bone.use_inherit_rotation == state['inherit_rotation']
            and bone.use_local_location == state['local_location'])


def _active(context, armature):
    if (armature is None or armature.type != 'ARMATURE' or context.object != armature
            or armature.mode not in {'OBJECT', 'POSE'} or armature.library or armature.data.library
            or not armature.is_editable or armature.data.users != 1):
        raise _error('Select a local, single-user armature in Object or Pose Mode.')


def _update(context, armature):
    armature.update_tag(refresh={'OBJECT'})
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()


def _animated(armature, names):
    curves = [c for action in _limb()._actions_for_id(armature) for c in _limb()._fcurves_for_action(action)]
    if armature.animation_data:
        curves.extend(armature.animation_data.drivers)
    return any(_limb()._path_mentions_bone(c.data_path, names) for c in curves)


def _resolve_chain(context, armature, chain, hips_name):
    from . import character_setup
    if not hips_name:
        try:
            hips_name = character_setup.resolve_bone(context, 'HIPS', armature=armature)
        except ValueError as exc:
            raise _error(str(exc)) from exc
    hips = armature.data.bones.get(hips_name)
    if hips is None:
        raise _error('Choose the central Hips bone in Character Setup first.')
    if chain is None:
        chain, current = [], hips
        while True:
            candidates = [b for b in current.children if b.use_deform and not b.get(OWNER_KEY)
                          and any(word in b.name.lower() for word in ('spine', 'chest', 'ribcage'))]
            if not candidates:
                break
            if len(candidates) != 1:
                raise _error('More than one torso chain was found; specify its ordered spine bones.')
            current = candidates[0]
            chain.append(current.name)
    chain = list(chain)
    if len(chain) not in {3, 4} or len(set(chain)) != len(chain):
        raise _error('Torso Controls needs three or four connected spine segments above Hips.')
    parent = hips
    for name in chain:
        bone = armature.data.bones.get(name)
        if (bone is None or bone.parent != parent or not bone.use_deform or bone.get(OWNER_KEY)
                or bone.length < 1e-5 or not bone.use_inherit_rotation):
            raise _error('Choose an ordered native spine chain directly above Hips.')
        parent = bone
    return chain, hips_name


def _add_constraint(record, owner, kind, name, armature, **fields):
    con = owner.constraints.new(kind)
    con.name, con.target = name, armature
    for key, value in fields.items():
        setattr(con, key, value)
    record['constraints'].append({'owner': owner.name, 'name': con.name, 'type': kind, 'fields': fields})
    return con


def _add_widget(context, armature, record, role, width):
    collection = bpy.data.collections.get(record['widget_collection'])
    if collection is None:
        collection = bpy.data.collections.new(record['widget_collection'])
        context.scene.collection.children.link(collection)
        _tag(collection, record, 'WIDGET_COLLECTION')
    # The broad waist contour follows Rain's torso widget silhouette. FK rings
    # stay in their local transverse plane and do not affect deformation.
    vertices = []
    for i in range(32):
        angle = i * math.tau / 32
        x, z = math.cos(angle), math.sin(angle)
        y = 0.78 * abs(x) ** 1.8 if role == 'BEND' else 0.0
        vertices.append((x * 0.5, y, z * (0.32 if role == 'BEND' else 0.38)))
    name = 'WGT_CD_Torso_' + role + '_' + record['id'][:10]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [(i, (i + 1) % 32) for i in range(32)], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    _tag(obj, record, 'WIDGET')
    _tag(mesh, record, 'WIDGET_MESH')
    obj.hide_render, obj.hide_select = True, True
    obj.hide_set(True)
    pb = armature.pose.bones[record['bones'][role]]
    pb.custom_shape, pb.use_custom_shape_bone_size = obj, False
    pb.custom_shape_scale_xyz = (width,) * 3
    if hasattr(pb, 'custom_shape_wire_width'):
        pb.custom_shape_wire_width = 2.0
    record['widgets'][role] = {'object': obj.name, 'mesh': mesh.name}


def _verify_pose(armature, desired):
    for name, matrix in desired.items():
        current = armature.pose.bones[name].matrix
        if max(abs(current[i][j] - matrix[i][j]) for i in range(4) for j in range(4)) > 4e-4:
            raise _error(f"Torso Controls could not preserve bone '{name}' in this pose; no changes kept.")


def _delete_graph(context, armature, record):
    for entry in reversed(record['constraints']):
        owner = armature.pose.bones.get(entry['owner'])
        con = owner.constraints.get(entry['name']) if owner else None
        if con:
            owner.constraints.remove(con)
    _limb()._mode_set(context, armature, 'EDIT')
    for name in reversed(tuple(record['bones'].values())):
        bone = armature.data.edit_bones.get(name)
        if bone:
            armature.data.edit_bones.remove(bone)
    _limb()._mode_set(context, armature, 'OBJECT')
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


def build(context, armature, chain=None, hips_name=None):
    """Create zeroed FK animators around the current pose, keeping native rest data."""
    from . import bone_collections
    _active(context, armature)
    previous = validate(armature)
    if previous:
        if ((chain is not None and list(chain) != previous['sources'])
                or (hips_name is not None and hips_name != previous['hips'])):
            raise _error('Remove the existing Torso Controls before changing its spine chain.')
        return previous
    sources, hips_name = _resolve_chain(context, armature, chain, hips_name)
    if _animated(armature, sources):
        raise _error('The native spine has animation or drivers; preserve that animation before adding controls.')
    if any(armature.pose.bones[name].constraints for name in sources):
        raise _error('The native spine already has constraints; preserve that rig before adding controls.')
    groups = [c for c in armature.data.collections_all if c.get(_limb().OWNER_KEY) == _limb().OWNER_VALUE
              and c.get(_limb().ROLE_KEY) == 'CONTROL_COLLECTION']
    if len(groups) != 1:
        raise _error('Create the character limb controls first, so Torso Controls can share its Controls collection.')
    group_name = groups[0].name
    names = {'BEND': 'CTRL_torso_bend'}
    for i in range(len(sources)):
        names[f'OFFSET_{i}'], names[f'FK_{i}'] = f'MCH_torso_offset_{i + 1}', f'CTRL_torso_{i + 1}'
    if any(name in armature.data.bones for name in names.values()):
        raise _error('A Torso Controls bone name is already occupied.')
    _update(context, armature)
    desired = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    bases = {pb.name: pb.matrix_basis.copy() for pb in armature.pose.bones}
    rest = {name: _state(armature.data.bones[name]) for name in sources}
    record = {'version': VERSION, 'id': uuid.uuid4().hex, 'sources': sources, 'hips': hips_name,
              'controls': {name: names[f'FK_{i}'] for i, name in enumerate(sources)},
              'bend': names['BEND'], 'bones': names, 'bone_states': {}, 'source_states': rest,
              'constraints': [], 'widgets': {}}
    record['widget_collection'] = 'CD_Torso_Widgets_' + record['id'][:10]
    layout = bone_collections.capture_managed_layout(armature)
    ctx = _limb()._capture_context(context, armature)
    mirror = armature.data.use_mirror_x
    plans = [('BEND', hips_name, sources[0])]
    for i, source in enumerate(sources):
        plans += [(f'OFFSET_{i}', names[f'FK_{i-1}'] if i else hips_name, source),
                  (f'FK_{i}', names[f'OFFSET_{i}'], source)]
    try:
        armature.data.use_mirror_x = False
        _limb()._mode_set(context, armature, 'EDIT')
        for role, parent, source in plans:
            state = rest[source]
            bone = armature.data.edit_bones.new(names[role])
            bone.head, bone.tail = state['head'], state['tail']
            bone.align_roll(Matrix(state['matrix']).to_3x3().col[2])
            bone.parent = armature.data.edit_bones[parent]
            bone.use_connect, bone.use_deform = False, False
            bone.inherit_scale = state['inherit_scale']
        _limb()._mode_set(context, armature, 'OBJECT')
        for role, _parent, source in plans:
            pb = armature.pose.bones[names[role]]
            _tag(pb.bone, record, role)
            pb.rotation_mode = 'XYZ'
            hidden = role.startswith('OFFSET')
            pb.bone.hide, pb.bone.hide_select = hidden, hidden
            pb.lock_location = pb.lock_scale = (True,) * 3
            pb.lock_rotation = (hidden,) * 3
            if hidden:
                pb.matrix = desired[source]
            else:
                pb.matrix_basis = Matrix.Identity(4)
            _update(context, armature)
            record['bone_states'][role] = _state(pb.bone)
            armature.data.collections_all[group_name].assign(pb.bone)
        for i, source in enumerate(sources):
            _add_constraint(record, armature.pose.bones[names[f'OFFSET_{i}']], 'COPY_ROTATION',
                            'CD Torso Shared Bend', armature, subtarget=names['BEND'],
                            target_space='LOCAL', owner_space='LOCAL', mix_mode='AFTER', influence=1.0 / len(sources))
            _add_constraint(record, armature.pose.bones[source], 'COPY_TRANSFORMS',
                            'CD Torso FK', armature, subtarget=names[f'FK_{i}'],
                            target_space='WORLD', owner_space='WORLD', mix_mode='REPLACE', influence=1.0)
        width = sum(armature.data.bones[name].length for name in sources) * 0.95
        for role in ('BEND', *(f'FK_{i}' for i in range(len(sources)))):
            _add_widget(context, armature, record, role, width * (1.17 if role == 'BEND' else 0.90))
        armature.data[RECORD_KEY] = json.dumps(record)
        _update(context, armature)
        _verify_pose(armature, desired)
        validate(armature)
        bone_collections.finish_rig_edit(armature, layout)
    except Exception:
        if RECORD_KEY in armature.data:
            del armature.data[RECORD_KEY]
        _delete_graph(context, armature, record)
        for name, matrix in bases.items():
            armature.pose.bones[name].matrix_basis = matrix
        _update(context, armature)
        raise
    finally:
        armature.data.use_mirror_x = mirror
        _limb()._restore_context(context, armature, ctx)
    from . import control_colors
    for role in record['widgets']:
        control_colors.style(armature.pose.bones[record['bones'][role]])
    return record


def _refuse_dependencies(armature, record):
    from .foot_controls import _driver_owners
    names = set(record['bones'].values())
    if _animated(armature, names | set(record['sources'])):
        raise _error('Torso Controls has animation or drivers; preserve those channels before removing it.')
    for bone in armature.data.bones:
        if bone.name not in names and bone.parent and bone.parent.name in names:
            raise _error('Another bone follows Torso Controls; detach it before removal.')
    own_cons = {(e['owner'], e['name']) for e in record['constraints']}
    widgets = {e['object']: record['bones'][role] for role, e in record['widgets'].items()}
    for obj in bpy.data.objects:
        if obj.parent == armature and obj.parent_type == 'BONE' and obj.parent_bone in names:
            raise _error('An object follows Torso Controls; detach it before removal.')
        for con in obj.constraints:
            if _limb()._constraint_references_controls(con, armature, names):
                raise _error('An object constraint follows Torso Controls; detach it before removal.')
        if obj.type != 'ARMATURE':
            continue
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if obj == armature and (pb.name, con.name) in own_cons:
                    continue
                if _limb()._constraint_references_controls(con, armature, names):
                    raise _error('Another constraint follows Torso Controls; detach it before removal.')
            if obj == armature and pb.name not in names and pb.custom_shape_transform and pb.custom_shape_transform.name in names:
                raise _error('Another widget follows a Torso Controls bone; detach it before removal.')
            if pb.custom_shape and pb.custom_shape.name in widgets:
                if obj != armature or pb.name != widgets[pb.custom_shape.name]:
                    raise _error('A Torso Controls widget is shared; make that use independent first.')
    for owner in _driver_owners():
        animation = getattr(owner, 'animation_data', None)
        for curve in animation.drivers if animation else ():
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id == armature and (target.bone_target in names or _limb()._path_mentions_bone(target.data_path, names)):
                        raise _error('An external driver reads Torso Controls; preserve that setup first.')
    collection = bpy.data.collections[record['widget_collection']]
    if collection.children or collection.users > 1 or set(collection.objects.keys()) != set(widgets):
        raise _error('The Torso Controls widget collection contains artist data; separate it before removal.')
    for entry in record['widgets'].values():
        obj = bpy.data.objects[entry['object']]
        if obj.data.users != 1 or tuple(obj.users_collection) != (collection,) or obj.users > 2:
            raise _error('A Torso Controls widget is shared; make that use independent first.')


def remove(context, armature):
    """Match the current native pose before removing the optional controller graph."""
    from . import bone_collections
    _active(context, armature)
    record = validate(armature)
    if record is None:
        raise _error('This armature has no Torso Controls to remove.')
    _refuse_dependencies(armature, record)
    _update(context, armature)
    names = set(record['bones'].values())
    desired = {pb.name: pb.matrix.copy() for pb in armature.pose.bones if pb.name not in names}
    bases = {name: armature.pose.bones[name].matrix_basis.copy() for name in record['sources']}
    layout = bone_collections.capture_managed_layout(armature)
    ctx = _limb()._capture_context(context, armature)
    mirror = armature.data.use_mirror_x
    try:
        for name in record['sources']:
            armature.pose.bones[name].constraints['CD Torso FK'].mute = True
        _update(context, armature)
        for name in record['sources']:
            armature.pose.bones[name].matrix = desired[name]
            _update(context, armature)
        _verify_pose(armature, desired)
    except Exception:
        for name, matrix in bases.items():
            armature.pose.bones[name].matrix_basis = matrix
            armature.pose.bones[name].constraints['CD Torso FK'].mute = False
        _update(context, armature)
        raise
    try:
        armature.data.use_mirror_x = False
        _delete_graph(context, armature, record)
        del armature.data[RECORD_KEY]
        bone_collections.finish_rig_edit(armature, layout)
        _update(context, armature)
        _verify_pose(armature, desired)
    finally:
        armature.data.use_mirror_x = mirror
        _limb()._restore_context(context, armature, ctx)
    from . import control_colors
    control_colors.cleanup(armature)
    return {'bones_removed': len(names), 'pose_preserved': True}
