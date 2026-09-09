"""Removable, head-relative eye targets with a shared gaze control."""
from __future__ import annotations

import json
import math
import uuid

import bpy
from mathutils import Matrix, Vector

from .torso_controls import _active, _animated, _same_rest, _same_value, _state, _update

OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'eye_controls'
ROLE_KEY = 'character_designer_eye_role'
ID_KEY = 'character_designer_eye_id'
RECORD_KEY = 'character_designer_eye_controls_v1'
VERSION = 1
ROLES = ('MASTER', 'LEFT', 'RIGHT')


def _limb():
    from . import limb_ik
    return limb_ik


def _error(message):
    return _limb().LimbIKError(message)


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
        if (record['version'] != VERSION or set(record['bones']) != set(ROLES)
                or len(set(record['bones'].values())) != 3 or len(record['sources']) != 2
                or len(set(record['sources'])) != 2 or set(record['targets']) != {'L', 'R'}
                or record['master'] != record['bones']['MASTER']
                or record['targets'] != {'L': record['bones']['LEFT'], 'R': record['bones']['RIGHT']}
                or set(record['source_states']) != {*record['sources'], record['head']}
                or set(record['bone_states']) != set(ROLES) or set(record['widgets']) != set(ROLES)
                or len(record['constraints']) != 2):
            raise ValueError('unsupported structure')
        spacing = record.get('display_spacing')
        if spacing is not None:
            if (not math.isfinite(spacing['distance']) or spacing['distance'] < 0
                    or set(spacing['original']) != set(ROLES)
                    or any(len(value) != 3 or not all(math.isfinite(x) for x in value)
                           for value in spacing['original'].values())):
                raise ValueError('invalid display spacing')
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error('Eye Controls recovery data is invalid; restore a saved copy.') from exc


def collection_members(armature):
    record = get_record(armature)
    if record is None:
        return {'generated': set(), 'replaced': set(), 'always': set(), 'visible': set()}
    names = set(record['bones'].values())
    return {'generated': names, 'replaced': set(record['sources']),
            'always': names, 'visible': names}


def resolve_eyes(context, armature, head_name=None, left_name=None, right_name=None):
    """Return (head, left eye, right eye), respecting the shared Head mapping."""
    from . import character_setup
    if armature is None or armature.type != 'ARMATURE':
        raise _error('Select the character armature first.')
    try:
        head_name = character_setup.resolve_bone(context, 'HEAD', armature=armature,
                                                override=head_name or '')
    except ValueError as exc:
        raise _error(str(exc)) from exc
    head = armature.data.bones.get(head_name)
    result = []
    for side, requested in (('L', left_name), ('R', right_name)):
        aliases = {'eye.' + side.lower(), 'eye_' + side.lower(), side.lower() + '_eye',
                   ('left' if side == 'L' else 'right') + '_eye',
                   ('left' if side == 'L' else 'right') + 'eye'}
        candidates = [b.name for b in armature.data.bones
                      if character_setup._bone_name(b.name) in aliases and not b.get(OWNER_KEY)]
        name = requested or (candidates[0] if len(candidates) == 1 else None)
        if not name:
            raise _error(f'Choose one native {"left" if side == "L" else "right"} eye bone.')
        bone = armature.data.bones.get(name)
        if (bone is None or bone.parent != head or not bone.use_deform or bone.get(OWNER_KEY)
                or bone.length < 1e-5 or not bone.use_inherit_rotation or bone.use_connect):
            raise _error('Choose two native, unconnected eye bones parented directly to the shared Head.')
        result.append(name)
    if result[0] == result[1] or head_name in result or head.get(OWNER_KEY):
        raise _error('Head, left eye and right eye must be distinct native bones.')
    return head_name, *result


def validate(armature, inventory=None):
    """Check owned resources and native rest data without recursing into inventory."""
    record = get_record(armature)
    actual = {b.name for b in armature.data.bones if b.get(OWNER_KEY) == OWNER_VALUE}
    if record is None:
        if actual:
            raise _error('Eye Controls bones have lost their recovery data.')
        return None
    try:
        names = set(record['bones'].values())
        if actual != names:
            raise _error('Eye Controls bones were added, removed, or renamed.')
        for role, name in record['bones'].items():
            bone = armature.data.bones[name]
            if not _owned(bone, record, role) or bone.use_deform or not _same_rest(bone, record['bone_states'][role]):
                raise _error(f"Eye Controls bone '{name}' was structurally edited.")
        for name, state in record['source_states'].items():
            bone = armature.data.bones.get(name)
            if bone is None or not _same_rest(bone, state) or bone.use_deform != state['deform']:
                raise _error(f"Eye Controls source '{name}' changed; restore its original structure first.")
        own_cons = {(entry['owner'], entry['name']) for entry in record['constraints']}
        for name in names | set(record['sources']):
            if any((name, con.name) not in own_cons for con in armature.pose.bones[name].constraints):
                raise _error(f"Bone '{name}' has additional constraints; preserve that setup first.")
        for index, entry in enumerate(record['constraints']):
            expected = {'subtarget': record['targets']['L' if index == 0 else 'R'],
                        'track_axis': 'TRACK_Y', 'head_tail': 0.0,
                        'target_space': 'WORLD', 'owner_space': 'WORLD', 'influence': 1.0}
            if (entry['owner'] != record['sources'][index] or entry['name'] != 'CD Eye Aim'
                    or entry['type'] != 'DAMPED_TRACK' or entry['fields'] != expected):
                raise _error('Eye Controls aim recovery data was edited.')
            con = armature.pose.bones[entry['owner']].constraints.get(entry['name'])
            if (con is None or con.type != 'DAMPED_TRACK' or con.target != armature or con.mute
                    or any(not _same_value(getattr(con, key), value) for key, value in expected.items())):
                raise _error(f"Eye constraint on '{entry['owner']}' was edited.")
        for role, entry in record['widgets'].items():
            obj = bpy.data.objects.get(entry['object'])
            pb = armature.pose.bones[record['bones'][role]]
            if (obj is None or not _owned(obj, record, 'WIDGET') or obj.type != 'MESH'
                    or obj.data.name != entry['mesh'] or not _owned(obj.data, record, 'WIDGET_MESH')
                    or pb.custom_shape != obj or pb.custom_shape_transform is not None):
                raise _error('An Eye Controls widget was replaced or removed.')
        collection = bpy.data.collections.get(record['widget_collection'])
        if collection is None or not _owned(collection, record, 'WIDGET_COLLECTION'):
            raise _error('The Eye Controls widget collection is missing.')
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise _error('Eye Controls recovery data no longer matches this rig.') from exc
    return record


def _verify_pose(armature, desired):
    for name, matrix in desired.items():
        current = armature.pose.bones[name].matrix
        if max(abs(current[i][j] - matrix[i][j]) for i in range(4) for j in range(4)) > 4e-4:
            raise _error(f"Eye Controls could not preserve bone '{name}' in this pose; no changes kept.")


def recommended_display_spacing(armature):
    record = validate(armature)
    if record is None:
        raise _error('Add Eye Controls first.')
    return armature.data.bones[record['head']].length * 2.0


def display_spacing(armature):
    record = validate(armature)
    if record is None:
        raise _error('Add Eye Controls first.')
    return record.get('display_spacing', {}).get('distance', 0.0)


def set_display_spacing(context, armature, distance):
    """Offset clickable shapes in front of their targets without changing gaze."""
    _active(context, armature)
    record = validate(armature)
    if record is None:
        raise _error('Add Eye Controls first.')
    if not math.isfinite(distance) or distance < 0:
        raise _error('Display spacing must be a finite, non-negative distance.')
    paths = {armature.pose.bones[name].path_from_id('custom_shape_translation')
             for name in record['bones'].values()}
    curves = [curve for action in _limb()._actions_for_id(armature)
              for curve in _limb()._fcurves_for_action(action)]
    if armature.animation_data:
        curves.extend(armature.animation_data.drivers)
    if any(curve.data_path in paths for curve in curves):
        raise _error('Eye display positions have animation or drivers; preserve those channels first.')
    previous = {role: list(armature.pose.bones[name].custom_shape_translation)
                for role, name in record['bones'].items()}
    original = record.get('display_spacing', {}).get('original', previous)
    old_raw = armature.data[RECORD_KEY]
    try:
        for role, name in record['bones'].items():
            # Generated eye targets share a backward local Y axis. Blender applies
            # custom translation BEFORE custom rotation and shape/bone-size scale,
            # so this moves all three outlines forward without scaling the offset.
            offset = Vector(original[role]) + Vector((0.0, -distance, 0.0))
            armature.pose.bones[name].custom_shape_translation = offset
        if distance:
            record['display_spacing'] = {'distance': distance, 'original': original}
        else:
            record.pop('display_spacing', None)
        armature.data[RECORD_KEY] = json.dumps(record)
        _update(context, armature)
    except Exception:
        for role, name in record['bones'].items():
            armature.pose.bones[name].custom_shape_translation = previous[role]
        armature.data[RECORD_KEY] = old_raw
        _update(context, armature)
        raise
    return record


def _add_widget(context, armature, record, role, width, height):
    collection = bpy.data.collections.get(record['widget_collection'])
    if collection is None:
        collection = bpy.data.collections.new(record['widget_collection'])
        context.scene.collection.children.link(collection)
        _tag(collection, record, 'WIDGET_COLLECTION')
    # Original analytic geometry: a pinched goggles contour and two clean rings.
    vertices = []
    for index in range(64):
        angle = index * math.tau / 64
        x, z = math.cos(angle), math.sin(angle)
        if role == 'MASTER':
            z *= 0.48 + 0.52 * abs(x) ** 0.55
        vertices.append((width * x, 0.0, height * z))
    name = 'WGT_CD_Eyes_' + role + '_' + record['id'][:10]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [(i, (i + 1) % 64) for i in range(64)], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    _tag(obj, record, 'WIDGET')
    _tag(mesh, record, 'WIDGET_MESH')
    # Register resources before display assignment so a failed assignment rolls back.
    record['widgets'][role] = {'object': obj.name, 'mesh': mesh.name}
    obj.hide_render, obj.hide_select = True, True
    obj.hide_set(True)
    pb = armature.pose.bones[record['bones'][role]]
    pb.custom_shape, pb.use_custom_shape_bone_size = obj, False
    if hasattr(pb, 'custom_shape_wire_width'):
        pb.custom_shape_wire_width = 2.0


def _add_constraint(record, armature, source, target):
    fields = {'subtarget': target, 'track_axis': 'TRACK_Y', 'head_tail': 0.0,
              'target_space': 'WORLD', 'owner_space': 'WORLD', 'influence': 1.0}
    con = armature.pose.bones[source].constraints.new('DAMPED_TRACK')
    con.name, con.target = 'CD Eye Aim', armature
    record['constraints'].append({'owner': source, 'name': con.name, 'type': con.type, 'fields': fields})
    for key, value in fields.items():
        setattr(con, key, value)


def _delete_graph(context, armature, record, *, keep_widgets=False):
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
    if not keep_widgets:
        _delete_widgets(record)


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


def build(context, armature, head_name=None, left_name=None, right_name=None):
    """Keep the current gaze; zero target translations always refer to native rest axes."""
    from . import bone_collections
    _active(context, armature)
    previous = validate(armature)
    if previous:
        if any(value is not None and value != expected for value, expected in zip(
                (head_name, left_name, right_name), (previous['head'], *previous['sources']))):
            raise _error('Remove the existing Eye Controls before changing its eye bones.')
        return previous
    head_name, left_name, right_name = resolve_eyes(context, armature, head_name, left_name, right_name)
    sources = [left_name, right_name]
    if _animated(armature, set(sources)):
        raise _error('The native eyes have animation or drivers; preserve those channels before adding Eye Controls.')
    if any(armature.pose.bones[name].constraints for name in sources):
        raise _error('The native eyes already have constraints; preserve that rig before adding Eye Controls.')
    groups = [c for c in armature.data.collections_all if c.get(_limb().OWNER_KEY) == _limb().OWNER_VALUE
              and c.get(_limb().ROLE_KEY) == 'CONTROL_COLLECTION']
    if len(groups) != 1:
        raise _error('Create the character limb controls first, so Eye Controls can share its Controls collection.')
    names = dict(zip(ROLES, ('CTRL_eyes', 'CTRL_eye.L', 'CTRL_eye.R')))
    if any(name in armature.data.bones for name in names.values()):
        raise _error('An Eye Controls bone name is already occupied.')
    _update(context, armature)
    desired = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    bases = {pb.name: pb.matrix_basis.copy() for pb in armature.pose.bones}
    eyes = [armature.data.bones[name] for name in sources]
    spacing = (eyes[0].head_local - eyes[1].head_local).length
    axes = [(bone.tail_local - bone.head_local).normalized() for bone in eyes]
    forward = axes[0] + axes[1]
    if spacing < 1e-5 or forward.length < 1e-5:
        raise _error('The eyes need separate origins and compatible forward axes.')
    forward.normalize()
    right = eyes[0].head_local - eyes[1].head_local
    right -= forward * right.dot(forward)
    if right.length < 1e-5:
        raise _error('The eye origins must lie across the face, away from their forward axis.')
    right.normalize()
    backward = -forward
    up = right.cross(backward).normalized()
    eye_length = max(b.length for b in eyes)
    distance = max(spacing * 2.0, eye_length * 1.5,
                   armature.data.bones[head_name].length * 0.65)
    points = [bone.head_local + axis * distance for bone, axis in zip(eyes, axes)]
    center = (points[0] + points[1]) * 0.5
    record = {'version': VERSION, 'id': uuid.uuid4().hex, 'head': head_name, 'sources': sources,
              'master': names['MASTER'], 'targets': {'L': names['LEFT'], 'R': names['RIGHT']},
              'bones': names, 'bone_states': {}, 'source_states': {
                  name: _state(armature.data.bones[name]) for name in (head_name, *sources)},
              'constraints': [], 'widgets': {}, 'distance': distance}
    record['widget_collection'] = 'CD_Eye_Widgets_' + record['id'][:10]
    layout = bone_collections.capture_managed_layout(armature)
    ctx = _limb()._capture_context(context, armature)
    mirror = armature.data.use_mirror_x
    plans = [('MASTER', head_name, center), ('LEFT', names['MASTER'], points[0]),
             ('RIGHT', names['MASTER'], points[1])]
    try:
        armature.data.use_mirror_x = False
        _limb()._mode_set(context, armature, 'EDIT')
        for role, parent_name, point in plans:
            bone = armature.data.edit_bones.new(names[role])
            bone.head, bone.tail = point, point + backward * max(spacing * 0.3, 0.01)
            bone.align_roll(up)
            bone.parent = armature.data.edit_bones[parent_name]
            bone.use_connect, bone.use_deform = False, False
        _limb()._mode_set(context, armature, 'OBJECT')
        for role, _parent, _point in plans:
            pb = armature.pose.bones[names[role]]
            _tag(pb.bone, record, role)
            pb.rotation_mode = 'XYZ'
            pb.lock_rotation = pb.lock_scale = (True, True, True)
            pb.lock_location = (False, False, False)
            pb.bone.hide = pb.bone.hide_select = False
            record['bone_states'][role] = _state(pb.bone)
            groups[0].assign(pb.bone)
        _update(context, armature)
        # Position only the visible targets. Their edit-bone rest frames are never
        # fitted to the current pose, so Alt-G restores the native neutral gaze.
        for role, source in zip(('LEFT', 'RIGHT'), sources):
            pb = armature.pose.bones[names[role]]
            wanted = pb.matrix.copy()
            wanted.translation = desired[source].translation + desired[source].to_3x3().col[1].normalized() * distance
            pb.matrix = wanted
            _update(context, armature)
        for source, side in zip(sources, ('L', 'R')):
            _add_constraint(record, armature, source, record['targets'][side])
        target_spacing = abs((points[0] - points[1]).dot(right))
        radius = max(spacing * 0.23, eye_length * 0.20, target_spacing * 0.18)
        half_width = target_spacing * 0.5 + radius * 1.8
        _add_widget(context, armature, record, 'MASTER', half_width, radius * 2.6)
        for role in ('LEFT', 'RIGHT'):
            _add_widget(context, armature, record, role, radius, radius)
        armature.data[RECORD_KEY] = json.dumps(record)
        record = set_display_spacing(context, armature,
                                     armature.data.bones[head_name].length * 2.0)
        _update(context, armature)
        _verify_pose(armature, desired)
        validate(armature)
        bone_collections.finish_rig_edit(armature, layout)
    except Exception:
        armature.data.pop(RECORD_KEY, None)
        _delete_graph(context, armature, record)
        for name, matrix in bases.items():
            armature.pose.bones[name].matrix_basis = matrix
        bone_collections.restore_layout(armature, layout)
        _update(context, armature)
        raise
    finally:
        armature.data.use_mirror_x = mirror
        _limb()._restore_context(context, armature, ctx)
    from . import control_colors
    for name in names.values():
        control_colors.style(armature.pose.bones[name])
    return record


def _refuse_dependencies(armature, record):
    from .foot_controls import _driver_owners
    names = set(record['bones'].values())
    if _animated(armature, names | set(record['sources'])):
        raise _error('Eye Controls has animation or drivers; preserve those channels before removal.')
    for bone in armature.data.bones:
        if bone.name not in names and bone.parent and bone.parent.name in names:
            raise _error('Another bone follows Eye Controls; detach it before removal.')
    own_cons = {(entry['owner'], entry['name']) for entry in record['constraints']}
    widgets = {entry['object']: record['bones'][role] for role, entry in record['widgets'].items()}
    for obj in bpy.data.objects:
        if obj.parent == armature and obj.parent_type == 'BONE' and obj.parent_bone in names:
            raise _error('An object follows Eye Controls; detach it before removal.')
        for con in obj.constraints:
            if _limb()._constraint_references_controls(con, armature, names):
                raise _error('An object constraint follows Eye Controls; detach it before removal.')
        if obj.type != 'ARMATURE':
            continue
        for pb in obj.pose.bones:
            for con in pb.constraints:
                if obj == armature and (pb.name, con.name) in own_cons:
                    continue
                if _limb()._constraint_references_controls(con, armature, names):
                    raise _error('Another constraint follows Eye Controls; detach it before removal.')
            if obj == armature and pb.name not in names and pb.custom_shape_transform and pb.custom_shape_transform.name in names:
                raise _error('Another widget follows an Eye Controls bone; detach it before removal.')
            if pb.custom_shape and pb.custom_shape.name in widgets:
                if obj != armature or pb.name != widgets[pb.custom_shape.name]:
                    raise _error('An Eye Controls widget is shared; make that use independent first.')
    for owner in _driver_owners():
        animation = getattr(owner, 'animation_data', None)
        for curve in animation.drivers if animation else ():
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id == armature and (target.bone_target in names or _limb()._path_mentions_bone(target.data_path, names)):
                        raise _error('An external driver reads Eye Controls; preserve that setup first.')
    collection = bpy.data.collections[record['widget_collection']]
    if collection.children or collection.users > 1 or set(collection.objects.keys()) != set(widgets):
        raise _error('The Eye Controls widget collection contains artist data; separate it before removal.')
    for entry in record['widgets'].values():
        obj = bpy.data.objects[entry['object']]
        if obj.data.users != 1 or tuple(obj.users_collection) != (collection,) or obj.users > 2:
            raise _error('An Eye Controls widget is shared; make that use independent first.')


_DISPLAY_FIELDS = ('lock_location', 'lock_rotation', 'lock_scale', 'lock_rotation_w',
                   'lock_rotations_4d', 'custom_shape_scale_xyz', 'custom_shape_translation',
                   'custom_shape_rotation_euler', 'use_custom_shape_bone_size', 'custom_shape_wire_width')


def _properties(item):
    return {key: value.to_dict() if hasattr(value, 'to_dict') else value.to_list()
            if hasattr(value, 'to_list') else value for key, value in item.items()}


def _controller_snapshot(armature, record):
    result = {}
    for role, name in record['bones'].items():
        pb = armature.pose.bones[name]
        fields = {field: tuple(value) if hasattr(value, '__len__') else value
                  for field in _DISPLAY_FIELDS if hasattr(pb, field) for value in (getattr(pb, field),)}
        result[role] = {'basis': pb.matrix_basis.copy(), 'rotation_mode': pb.rotation_mode,
                        'fields': fields, 'hide': pb.bone.hide, 'hide_select': pb.bone.hide_select,
                        'bone_properties': _properties(pb.bone), 'pose_properties': _properties(pb),
                        'colors': [(item.color.palette, item.color.custom.show_colored_constraints,
                                    [tuple(getattr(item.color.custom, field))
                                    for field in ('normal', 'select', 'active')]) for item in (pb.bone, pb)]}
    return result


def _restore_graph(context, armature, record, snapshot):
    """Reconstruct only owned bones after a failed removal; widget IDs stay alive."""
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
        pb.custom_shape = bpy.data.objects[record['widgets'][role]['object']]
        for item, (palette, colored_constraints, colors) in zip((pb.bone, pb), state['colors']):
            item.color.palette = palette
            item.color.custom.show_colored_constraints = colored_constraints
            for field, color in zip(('normal', 'select', 'active'), colors):
                setattr(item.color.custom, field, color)
    for entry in record['constraints']:
        pb = armature.pose.bones[entry['owner']]
        con = pb.constraints.get(entry['name']) or pb.constraints.new(entry['type'])
        con.name, con.target, con.mute = entry['name'], armature, False
        for field, value in entry['fields'].items():
            setattr(con, field, value)
    armature.data[RECORD_KEY] = json.dumps(record)


def remove(context, armature):
    """Match the visible gaze into native eye pose channels before removing targets."""
    from . import bone_collections
    _active(context, armature)
    record = validate(armature)
    if record is None:
        raise _error('This armature has no Eye Controls to remove.')
    _refuse_dependencies(armature, record)
    _update(context, armature)
    names = set(record['bones'].values())
    desired = {pb.name: pb.matrix.copy() for pb in armature.pose.bones if pb.name not in names}
    bases = {name: armature.pose.bones[name].matrix_basis.copy() for name in record['sources']}
    layout = bone_collections.capture_managed_layout(armature)
    ctx = _limb()._capture_context(context, armature)
    mirror = armature.data.use_mirror_x
    controllers = _controller_snapshot(armature, record)
    try:
        for name in record['sources']:
            armature.pose.bones[name].constraints['CD Eye Aim'].mute = True
        _update(context, armature)
        for name in record['sources']:
            armature.pose.bones[name].matrix = desired[name]
        _update(context, armature)
        _verify_pose(armature, desired)
    except Exception:
        for name, matrix in bases.items():
            armature.pose.bones[name].matrix_basis = matrix
            armature.pose.bones[name].constraints['CD Eye Aim'].mute = False
        _update(context, armature)
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
        for name, matrix in bases.items():
            armature.pose.bones[name].matrix_basis = matrix
        bone_collections.restore_layout(armature, layout)
        _update(context, armature)
        raise
    finally:
        armature.data.use_mirror_x = mirror
        _limb()._restore_context(context, armature, ctx)
    _delete_widgets(record)
    from . import control_colors
    control_colors.cleanup(armature)
    return {'bones_removed': len(names), 'pose_preserved': True}
