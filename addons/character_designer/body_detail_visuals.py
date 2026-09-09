"""Reversible breast and pelvis ring displays on the existing native pivots."""
from __future__ import annotations

import json
import math
import uuid

import bpy
from mathutils import Matrix, Vector

from . import character_setup, control_colors, limb_fk_visuals as visuals
from .torso_controls import _active, _same_rest, _state, _update

OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'body_detail_visuals'
ROLE_KEY = 'character_designer_body_detail_role'
ID_KEY = 'character_designer_body_detail_visual_id'
RECORD_KEY = 'character_designer_body_detail_visuals_v1'
REFERENCE_KEY = 'character_designer_body_detail_original_shapes'
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


def resolve_bones(context, armature, hips_name=None, left_name=None, right_name=None):
    """Return the three native roles; ambiguous names require explicit selection."""
    try:
        hips_name = character_setup.resolve_bone(context, 'HIPS', armature, hips_name or '')
    except ValueError as exc:
        raise _error(str(exc)) from exc
    result = {'HIPS': hips_name}
    for role, side, requested in (('BREAST_L', 'l', left_name), ('BREAST_R', 'r', right_name)):
        aliases = {f'breast.{side}', f'breast_{side}', f'{side}_breast',
                   'left_breast' if side == 'l' else 'right_breast'}
        candidates = ([requested] if requested else
                      [b.name for b in armature.data.bones if character_setup._bone_name(b.name) in aliases])
        if len(candidates) != 1 or candidates[0] not in armature.data.bones:
            raise _error('Choose one native left breast and one native right breast bone; Chest spine bones are not automatic candidates.')
        result[role] = candidates[0]
    if len(set(result.values())) != 3:
        raise _error('Choose three different native Hips and breast bones.')
    if any(armature.data.bones[name].get(OWNER_KEY) or armature.data.bones[name].length < 1e-5
           for name in result.values()):
        raise _error('Choose existing native Hips and breast bones with usable lengths.')
    return result


def get_record(armature):
    raw = armature.data.get(RECORD_KEY)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        if (record['version'] != VERSION or record['id'] != armature.data.get(ID_KEY)
                or set(record['names']) != {'HIPS', 'BREAST_L', 'BREAST_R'}
                or len(set(record['names'].values())) != 3
                or set(record['bindings']) != set(record['names'].values())):
            raise ValueError('unsupported record')
        for name, entry in record['bindings'].items():
            if record['names'].get(entry['role']) != name:
                raise ValueError('invalid role')
            if not all(isinstance(entry[key], str) for key in ('object', 'mesh')):
                raise ValueError('invalid resource name')
            _limb()._validate_shape_state(entry['original'], 'Saved Breast/Hips display')
            if not all(isinstance(entry['original'].get(key), str)
                       for key in ('custom_shape', 'custom_shape_transform')):
                raise ValueError('invalid original reference')
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error('Breast/Hips visual recovery data is invalid; restore a saved copy.') from exc


def _original_state(armature, entry):
    """Resolve saved ID references, retaining even unlinked artist widgets on save."""
    state = dict(entry['original'])
    refs = armature.data.get(REFERENCE_KEY, {})
    shape = refs.get(entry['role'])
    if bool(state['custom_shape']) != bool(shape) or (shape and shape.name not in bpy.data.objects):
        raise _error('An original Breast/Hips custom shape is missing; restore it before removal.')
    transform = armature.pose.bones.get(state['custom_shape_transform']) if state['custom_shape_transform'] else None
    if state['custom_shape_transform'] and transform is None:
        raise _error('An original Breast/Hips display anchor is missing; restore it before removal.')
    state['custom_shape'], state['custom_shape_transform'] = shape, transform
    return state


def validate(armature):
    """Validate ownership and native pivots; allow artist edits to widget/display geometry."""
    record = get_record(armature)
    if record is None:
        if ID_KEY in armature.data or REFERENCE_KEY in armature.data:
            raise _error('Breast/Hips widgets have lost their recovery data.')
        return None
    try:
        objects = {obj.name for obj in bpy.data.objects
                   if obj.get(OWNER_KEY) == OWNER_VALUE and obj.get(ID_KEY) == record['id']}
        if objects != {entry['object'] for entry in record['bindings'].values()}:
            raise _error('Breast/Hips widgets were added, removed or renamed.')
        if not _owned(bpy.data.collections.get(record['collection']), record, 'COLLECTION'):
            raise _error('The Breast/Hips widget collection is missing or was replaced.')
        for name, entry in record['bindings'].items():
            pb, obj = armature.pose.bones.get(name), bpy.data.objects.get(entry['object'])
            if (pb is None or pb.bone.get(OWNER_KEY) or not _same_rest(pb.bone, entry['rest'])
                    or pb.bone.use_deform != entry['rest']['deform']
                    or not _owned(obj, record, entry['role']) or obj.type != 'MESH'
                    or obj.data.name != entry['mesh'] or not _owned(obj.data, record, entry['role'])
                    or pb.custom_shape != obj):
                raise _error(f"Breast/Hips assignment or native pivot '{name}' changed; preserve that setup first.")
            _limb()._validate_shape_state(_limb()._pose_shape_json_state(pb), 'Breast/Hips display')
            _original_state(armature, entry)
    except (KeyError, TypeError, AttributeError) as exc:
        raise _error('Breast/Hips recovery data no longer matches this armature.') from exc
    return record


def _frame(hips, left, right):
    """Anatomical axes come from the torso and paired breasts, never breast roll."""
    parent = left.parent if left.parent == right.parent and left.parent is not None else hips
    up = (parent.tail_local-parent.head_local).normalized()
    forward = (left.tail_local-left.head_local) + (right.tail_local-right.head_local)
    forward -= up*forward.dot(up)
    if forward.length < 1e-5:
        lateral = left.head_local-right.head_local
        forward = up.cross(lateral)
        if forward.dot(Vector((0, -1, 0))) < 0:
            forward.negate()
    if forward.length < 1e-5:
        raise _error('The selected breast and torso bones do not define a usable anatomical frame.')
    forward.normalize()
    lateral = (-forward).cross(up).normalized()
    result = Matrix((lateral, -forward, up)).transposed().to_4x4()
    result.translation = hips.head_local
    return result


def _sources(context, armature, requested):
    state = character_setup.settings(context)
    shared = state is not None and state.rig == armature
    body = requested if requested is not None else state.body if shared else None
    if body is not None and (body.type != 'MESH' or body.name not in context.scene.objects
            or not any(mod.type == 'ARMATURE' and mod.object == armature for mod in body.modifiers)):
        if requested is not None:
            raise _error('Use a body mesh bound to this armature for Breast/Hips fitting.')
        body = None
    clothing, skirts = [], []
    if shared:
        if any(item.object == body and item.role == 'HAIR' for item in state.assets):
            body = None
        for item in state.assets:
            obj = item.object
            if obj is None or obj == body or obj.type != 'MESH' or obj.name not in context.scene.objects:
                continue
            if item.role == 'CLOTHING' and any(mod.type == 'ARMATURE' and mod.object == armature for mod in obj.modifiers):
                clothing.append(obj)
            elif item.role == 'SKIRT':
                skirts.append(obj)
    return body, tuple(dict.fromkeys(clothing)), tuple(dict.fromkeys(skirts))


def _points(armature, obj, frame, name=None):
    if obj is None:
        return []
    group = obj.vertex_groups.get(name) if name else None
    if name and group is None:
        return []
    matrix = frame.inverted() @ armature.matrix_world.inverted_safe() @ obj.matrix_world
    return [matrix @ v.co for v in obj.data.vertices
            if not name or any(g.group == group.index and g.weight >= .25 for g in v.groups)]


def _bounds(points):
    return ([min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)])


def _fit(context, armature, names, body_source):
    hips, left, right = (armature.data.bones[names[role]] for role in ('HIPS', 'BREAST_L', 'BREAST_R'))
    frame = _frame(hips, left, right)
    inverse = frame.inverted()
    body, clothing, skirts = _sources(context, armature, body_source)
    body_points = _points(armature, body, frame)
    clothed = [p for obj in clothing for p in _points(armature, obj, frame)]
    lengths = [left.length, right.length]
    centers = [inverse @ b.head_local.lerp(b.tail_local, .8) for b in (left, right)]
    separation = abs(centers[0].x-centers[1].x)
    if separation < min(lengths)*.35:
        raise _error('The breast targets are too close to fit two separately selectable rings.')
    fit = {'frame': [list(row) for row in frame], 'body': body.name if body else '',
           'clothing': [obj.name for obj in clothing], 'skirts': [obj.name for obj in skirts], 'roles': {}}
    for role, bone, center in zip(('BREAST_L', 'BREAST_R'), (left, right), centers):
        length = bone.length
        weighted = [p for p in _points(armature, body, frame, bone.name)
                    if abs(p.x-center.x) <= length*.85 and abs(p.z-center.z) <= length*.85
                    and abs(p.y-center.y) <= length*1.6]
        rx, rz = length*.53, length*.53
        if len(weighted) >= 8:
            low, high = _bounds(weighted)
            rx = max(length*.42, min(length*.58, (high[0]-low[0])*.5))
            rz = max(length*.42, min(length*.61, (high[2]-low[2])*.5))
        # Limit both radii by the actual pair spacing so the rings never overlap.
        rx = min(rx, separation*.42)
        surfaces = [p for p in body_points+clothed
                    if abs(p.x-center.x) <= rx*1.15 and abs(p.z-center.z) <= rz*1.15
                    and abs(p.y-center.y) <= length*1.8]
        front = min((p.y for p in surfaces), default=(inverse @ bone.tail_local).y)
        margin, cup = length*.10, length*.10
        center.y = front-margin-cup
        fit['roles'][role] = {'center': list(center), 'radius': [rx, rz], 'cup': cup,
            'profile': 'WRAP',
            'surface_front': front, 'margin': margin, 'weight_count': len(weighted),
            'surface_count': len(surfaces), 'method': 'WEIGHTS' if len(weighted) >= 8 else 'BONE'}
    height = hips.length*.25
    pelvis_points = body_points+clothed+[p for obj in skirts for p in _points(armature, obj, frame)]
    pelvis = [p for p in pelvis_points if abs(p.z-height) <= hips.length*.35
              and abs(p.x) <= hips.length*3.5 and abs(p.y) <= hips.length*3.5]
    margin = hips.length*.10
    if len(pelvis) >= 8:
        low, high = _bounds(pelvis)
        cx, cy = (low[0]+high[0])*.5, (low[1]+high[1])*.5
        rx, ry = (high[0]-low[0])*.5+margin, (high[1]-low[1])*.5+margin
        if min(rx, ry) < hips.length*.3:
            pelvis = []
    if len(pelvis) < 8:
        cx, cy, rx, ry = 0., 0., hips.length*1.65, hips.length*1.05
    fit['roles']['HIPS'] = {'center': [cx, cy, height], 'radius': [rx, ry],
        'dip': hips.length*.06, 'surface_count': len(pelvis), 'method': 'SLICE' if len(pelvis) >= 8 else 'BONE'}
    values = [v for data in fit['roles'].values() for key in ('center', 'radius') for v in data[key]]
    if not all(math.isfinite(value) for value in values):
        raise _error('Body fitting produced invalid dimensions; inspect the selected source meshes.')
    return fit


def _geometry(role, fit):
    data = fit['roles'][role]
    cx, cy, cz = data['center']
    rx, ry = data['radius']
    angles = [i*math.tau/64 for i in range(64)]
    if role == 'HIPS':
        vertices = [(cx+rx*math.cos(a), cy+ry*math.sin(a),
                     cz-data['dip']*max(0., -math.sin(a))**4) for a in angles]
    else:
        # +Y points toward the torso. The upper/lower rim sits farther back
        # than the middle, wrapping the outward breast profile in side view.
        # Unmarked saved shapes retain their legacy geometry until explicitly
        # updated, so removal and artist-edit detection remain reproducible.
        depth = math.sin if data.get('profile') == 'WRAP' else math.cos
        vertices = [(cx+rx*math.cos(a), cy+data['cup']*depth(a)**2,
                     cz+ry*math.sin(a)) for a in angles]
    return vertices, [(i, (i+1) % 64) for i in range(64)]


def _create_widget(context, armature, record, name, entry):
    collection = bpy.data.collections.get(record['collection'])
    if collection is None:
        collection = bpy.data.collections.new(record['collection'])
        context.scene.collection.children.link(collection)
        _tag(collection, record, 'COLLECTION')
    mesh = bpy.data.meshes.new(entry['mesh'])
    _tag(mesh, record, entry['role'])
    vertices, edges = _geometry(entry['role'], record['fit'])
    frame = Matrix(record['fit']['frame'])
    local = armature.data.bones[name].matrix_local.inverted() @ frame
    mesh.from_pydata([local @ Vector(vertex) for vertex in vertices], edges, [])
    mesh.update()
    obj = bpy.data.objects.new(entry['object'], mesh)
    _tag(obj, record, entry['role'])
    collection.objects.link(obj)
    obj.hide_render, obj.hide_select = True, True
    obj.hide_set(True)
    entry['object'], entry['mesh'] = obj.name, mesh.name
    state = {'custom_shape': obj, 'custom_shape_transform': None, 'use_bone_size': False,
             'scale': [1., 1., 1.], 'translation': [0., 0., 0.], 'rotation': [0., 0., 0.], 'wire_width': 2.0}
    _limb()._restore_pose_shape_state(armature, armature.pose.bones[name], state, runtime=True)


def _delete_resources(record):
    # Allocation may fail before Blender's collision-adjusted names reach the
    # record. The unique ownership ID also finds those partially made resources.
    roles = {entry['role'] for entry in record['bindings'].values()}
    for obj in tuple(bpy.data.objects):
        if (obj.get(OWNER_KEY) == OWNER_VALUE and obj.get(ID_KEY) == record['id']
                and obj.get(ROLE_KEY) in roles):
            bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in tuple(bpy.data.meshes):
        if (mesh.get(OWNER_KEY) == OWNER_VALUE and mesh.get(ID_KEY) == record['id']
                and mesh.get(ROLE_KEY) in roles and mesh.users == 0):
            bpy.data.meshes.remove(mesh)
    collection = bpy.data.collections.get(record['collection'])
    if _owned(collection, record, 'COLLECTION') and not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)


def build(context, armature, *, hips_name=None, left_name=None, right_name=None, body_source=None):
    """Fit three editable native displays without changing bones, weights or animation."""
    _active(context, armature)
    if previous := validate(armature):
        return update_breast_curvature(context, armature)
    names = resolve_bones(context, armature, hips_name, left_name, right_name)
    if any(visuals._animated_display(armature, armature.pose.bones[name]) for name in names.values()):
        raise _error('Breast/Hips custom-shape display has animation or drivers; preserve those channels first.')
    fit = _fit(context, armature, names, body_source)
    record = {'version': VERSION, 'id': uuid.uuid4().hex, 'names': names, 'bindings': {}, 'fit': fit}
    record['collection'] = 'CD_Body_Detail_Widgets_' + record['id'][:10]
    before, refs = {}, {}
    for role, name in names.items():
        pb = armature.pose.bones[name]
        before[name] = _limb()._pose_shape_runtime_state(pb)
        if pb.custom_shape:
            refs[role] = pb.custom_shape
        widget = 'WGT_CD_' + role + '_' + record['id'][:10]
        record['bindings'][name] = {'role': role, 'object': widget, 'mesh': widget,
            'original': _limb()._pose_shape_json_state(pb), 'original_color': control_colors.capture_bone(pb), 'rest': _state(pb.bone)}

    _update(context, armature)
    expected = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    try:
        armature.data[REFERENCE_KEY] = refs
        armature.data[ID_KEY] = record['id']
        for name, entry in record['bindings'].items():
            _create_widget(context, armature, record, name, entry)
        armature.data[RECORD_KEY] = json.dumps(record)
        _update(context, armature)
        visuals._verify_pose(armature, expected)
        validate(armature)
    except Exception:
        for name, state in before.items():
            _limb()._restore_pose_shape_state(armature, armature.pose.bones[name], state, runtime=True)
        _delete_resources(record)
        for key in (RECORD_KEY, REFERENCE_KEY, ID_KEY):
            armature.data.pop(key, None)
        _update(context, armature)
        raise
    return record


def update_breast_curvature(context, armature):
    """Explicitly reverse untouched legacy breast cups, preserving mesh IDs.

    Updated WRAP shapes are a no-op, including any later artist adjustments.
    Both old shapes are checked before either mesh or recovery data is changed.
    """
    _active(context, armature)
    record = validate(armature)
    if record is None:
        return None
    pending = [(name, entry) for name, entry in record['bindings'].items()
               if entry['role'] in {'BREAST_L', 'BREAST_R'}
               and record['fit']['roles'][entry['role']].get('profile') != 'WRAP']
    if not pending:
        return record
    changed = json.loads(json.dumps(record))
    before, replacement = {}, {}
    for name, entry in pending:
        role = entry['role']
        if record['fit']['roles'][role].get('profile') not in {None, 'LEGACY'}:
            raise _error('The breast widget profile is unsupported; preserve that setup before updating.')
        obj = bpy.data.objects[entry['object']]
        if (obj.data.users != 1 or obj.users > 2 or obj.animation_data is not None
                or obj.data.animation_data is not None or obj.data.shape_keys is not None
                or visuals._animated_display(armature, armature.pose.bones[name])):
            raise _error('A breast widget is shared or animated; preserve that use before updating.')
        vertices, edges = _geometry(role, record['fit'])
        local = armature.data.bones[name].matrix_local.inverted() @ Matrix(record['fit']['frame'])
        expected = [local @ Vector(vertex) for vertex in vertices]
        if (len(obj.data.vertices) != len(expected) or obj.data.polygons
                or len(obj.data.edges) != len(edges)
                or {tuple(sorted(edge.vertices)) for edge in obj.data.edges} != {tuple(sorted(edge)) for edge in edges}
                or any((vertex.co-point).length > 1e-6 for vertex, point in zip(obj.data.vertices, expected))):
            raise _error('A legacy breast widget has artist edits; preserve its shape before updating.')
        before[name] = [vertex.co.copy() for vertex in obj.data.vertices]
        changed['fit']['roles'][role]['profile'] = 'WRAP'
        vertices, _ = _geometry(role, changed['fit'])
        replacement[name] = [local @ Vector(vertex) for vertex in vertices]
    _update(context, armature)
    expected_pose = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    previous_raw = armature.data[RECORD_KEY]
    try:
        for name, points in replacement.items():
            mesh = bpy.data.objects[record['bindings'][name]['object']].data
            for vertex, point in zip(mesh.vertices, points):
                vertex.co = point
            mesh.update()
        armature.data[RECORD_KEY] = json.dumps(changed)
        _update(context, armature)
        visuals._verify_pose(armature, expected_pose)
        validate(armature)
    except Exception:
        for name, points in before.items():
            mesh = bpy.data.objects[record['bindings'][name]['object']].data
            for vertex, point in zip(mesh.vertices, points):
                vertex.co = point
            mesh.update()
        armature.data[RECORD_KEY] = previous_raw
        _update(context, armature)
        raise
    return changed


def _refuse_dependencies(armature, record):
    collection = bpy.data.collections[record['collection']]
    objects = {entry['object']: name for name, entry in record['bindings'].items()}
    if collection.children or collection.users > 1 or set(collection.objects.keys()) != set(objects):
        raise _error('The Breast/Hips widget collection is shared or contains artist data; separate it before removal.')
    for entry in record['bindings'].values():
        obj = bpy.data.objects[entry['object']]
        if (obj.data.users != 1 or obj.users > 2 or tuple(obj.users_collection) != (collection,)
                or obj.animation_data is not None or obj.data.animation_data is not None or obj.data.shape_keys is not None):
            raise _error('A Breast/Hips widget is shared or animated; preserve that use before removal.')
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            for pb in obj.pose.bones:
                if pb.custom_shape and pb.custom_shape.name in objects and (obj != armature or pb.name != objects[pb.custom_shape.name]):
                    raise _error('Another bone uses a Breast/Hips widget; preserve that use before removal.')
    if any(visuals._animated_display(armature, armature.pose.bones[name]) for name in record['bindings']):
        raise _error('Breast/Hips custom-shape display has animation or drivers; preserve those channels before removal.')


def remove(context, armature):
    """Restore preceding display/color states, including after editable-widget changes."""
    _active(context, armature)
    record = validate(armature)
    if record is None:
        return {'removed': 0}
    _refuse_dependencies(armature, record)
    _update(context, armature)
    expected = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    before = {name: (_limb()._pose_shape_runtime_state(armature.pose.bones[name]), control_colors.capture_bone(armature.pose.bones[name]))
              for name in record['bindings']}
    try:
        for name, entry in record['bindings'].items():
            pb = armature.pose.bones[name]
            _limb()._restore_pose_shape_state(armature, pb, _original_state(armature, entry), runtime=True)
            control_colors.restore_bone_state(pb, entry['original_color'])
        _update(context, armature)
        visuals._verify_pose(armature, expected)
    except Exception:
        for name, (state, color) in before.items():
            pb = armature.pose.bones[name]
            _limb()._restore_pose_shape_state(armature, pb, state, runtime=True)
            control_colors.restore_bone_state(pb, color)
        _update(context, armature)
        raise
    _delete_resources(record)
    for key in (RECORD_KEY, REFERENCE_KEY, ID_KEY):
        del armature.data[key]
    return {'removed': len(record['bindings'])}
