"""Reversible helmet and collar displays on the existing Head and Neck pivots."""
from __future__ import annotations

import json
import math
import uuid

import bpy
from mathutils import Matrix, Vector

from . import character_setup, control_colors, limb_fk_visuals as visuals
from .torso_controls import _active, _same_rest, _state, _update

OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'head_neck_visuals'
ROLE_KEY = 'character_designer_head_neck_role'
ID_KEY = 'character_designer_head_neck_visual_id'
RECORD_KEY = 'character_designer_head_neck_visuals_v1'
REFERENCE_KEY = 'character_designer_head_neck_original_shapes'
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


def resolve_bones(context, armature, head_name=None, neck_name=None):
    """Use the shared Head mapping and its native Neck parent, or explicit names."""
    try:
        head_name = character_setup.resolve_bone(context, 'HEAD', armature, head_name or '')
    except ValueError as exc:
        raise _error(str(exc)) from exc
    head = armature.data.bones[head_name]
    neck = armature.data.bones.get(neck_name) if neck_name else head.parent
    if (neck is None or head.parent != neck or head == neck
            or (not neck_name and character_setup._bone_name(neck.name) not in
                {'neck', 'neck.001', 'neck_01', 'neck01'})):
        raise _error('Choose the native Neck bone that directly parents the mapped Head.')
    if any(bone.get(OWNER_KEY) or bone.length < 1e-5 for bone in (head, neck)):
        raise _error('Choose existing native Head and Neck bones with usable lengths.')
    return head.name, neck.name


def get_record(armature):
    raw = armature.data.get(RECORD_KEY)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        if (record['version'] != VERSION or record['id'] != armature.data.get(ID_KEY)
                or set(record['bindings']) != {record['head'], record['neck']}
                or record['head'] == record['neck']):
            raise ValueError('unsupported record')
        for name, entry in record['bindings'].items():
            if entry['role'] != ('HEAD' if name == record['head'] else 'NECK'):
                raise ValueError('invalid role')
            if not all(isinstance(entry[key], str) for key in ('object', 'mesh')):
                raise ValueError('invalid resource name')
            _limb()._validate_shape_state(entry['original'], 'Saved Head/Neck display')
            if not all(isinstance(entry['original'].get(key), str)
                       for key in ('custom_shape', 'custom_shape_transform')):
                raise ValueError('invalid original reference')
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error('Head/Neck visual recovery data is invalid; restore a saved copy.') from exc


def _original_state(armature, entry):
    """Resolve saved ID references, retaining even unlinked artist widgets on save."""
    state = dict(entry['original'])
    refs = armature.data.get(REFERENCE_KEY, {})
    shape = refs.get(entry['role'])
    if bool(state['custom_shape']) != bool(shape) or (shape and shape.name not in bpy.data.objects):
        raise _error('An original Head/Neck custom shape is missing; restore it before removal.')
    transform = armature.pose.bones.get(state['custom_shape_transform']) if state['custom_shape_transform'] else None
    if state['custom_shape_transform'] and transform is None:
        raise _error('An original Head/Neck display anchor is missing; restore it before removal.')
    state['custom_shape'], state['custom_shape_transform'] = shape, transform
    return state


def validate(armature):
    """Validate ownership and native pivots; allow artist edits to widget/display geometry."""
    record = get_record(armature)
    if record is None:
        if ID_KEY in armature.data or REFERENCE_KEY in armature.data:
            raise _error('Head/Neck widgets have lost their recovery data.')
        return None
    try:
        objects = {obj.name for obj in bpy.data.objects
                   if obj.get(OWNER_KEY) == OWNER_VALUE and obj.get(ID_KEY) == record['id']}
        if objects != {entry['object'] for entry in record['bindings'].values()}:
            raise _error('Head/Neck widgets were added, removed or renamed.')
        if not _owned(bpy.data.collections.get(record['collection']), record, 'COLLECTION'):
            raise _error('The Head/Neck widget collection is missing or was replaced.')
        for name, entry in record['bindings'].items():
            pb, obj = armature.pose.bones.get(name), bpy.data.objects.get(entry['object'])
            if (pb is None or pb.bone.get(OWNER_KEY) or not _same_rest(pb.bone, entry['rest'])
                    or not _owned(obj, record, entry['role']) or obj.type != 'MESH'
                    or obj.data.name != entry['mesh'] or not _owned(obj.data, record, entry['role'])
                    or pb.custom_shape != obj):
                raise _error(f"Head/Neck assignment or native pivot '{name}' changed; preserve that setup first.")
            _limb()._validate_shape_state(_limb()._pose_shape_json_state(pb), 'Head/Neck display')
            _original_state(armature, entry)
    except (KeyError, TypeError, AttributeError) as exc:
        raise _error('Head/Neck recovery data no longer matches this armature.') from exc
    return record


def _frame(bone, forward):
    up = (bone.tail_local - bone.head_local).normalized()
    front = Vector(forward) - up * Vector(forward).dot(up)
    if front.length < 1e-5:
        front = bone.matrix_local.to_3x3().col[2]
    front.normalize()
    right = (-front).cross(up).normalized()
    result = Matrix((right, -front, up)).transposed().to_4x4()
    result.translation = bone.head_local
    return result


def _forward(armature, head):
    eyes = [bone for bone in head.children if not bone.get(OWNER_KEY)
            and 'eye' in character_setup._bone_name(bone.name)]
    direction = sum((bone.tail_local - bone.head_local for bone in eyes), Vector())
    return direction.normalized() if len(eyes) == 2 and direction.length > 1e-5 else Vector((0, -1, 0))


def _body(context, armature, requested):
    state = character_setup.settings(context)
    body = requested if requested is not None else state.body if state else None
    if body is None:
        return None
    if (body.type != 'MESH' or body.name not in context.scene.objects
            or not any(mod.type == 'ARMATURE' and mod.object == armature for mod in body.modifiers)):
        if requested is not None:
            raise _error('Use a body mesh bound to this armature for Head/Neck fitting.')
        return None
    if state and any(item.object == body and item.role == 'HAIR' for item in state.assets):
        return None
    return body


def _points(armature, body, name, frame, minimum=.5):
    if body is None or (group := body.vertex_groups.get(name)) is None:
        return []
    transform = frame.inverted() @ armature.matrix_world.inverted_safe() @ body.matrix_world
    return [transform @ vertex.co for vertex in body.data.vertices
            if any(item.group == group.index and item.weight >= minimum for item in vertex.groups)]


def _bounds(points):
    return [min(point[i] for point in points) for i in range(3)], [max(point[i] for point in points) for i in range(3)]


def _fit(context, armature, head, neck, body_source, bounds):
    forward = _forward(armature, head)
    head_frame, neck_frame = _frame(head, forward), _frame(neck, forward)
    body = _body(context, armature, body_source)
    length = head.length
    points = _points(armature, body, head.name, head_frame)
    # Body-only weights avoid separate long-hair assets. A generous anatomical
    # window also excludes long trailing strands if they share the body mesh.
    points = [p for p in points if -.35*length <= p.z <= 1.75*length
              and max(abs(p.x), abs(p.y)) <= 1.5*length]
    if bounds is not None:
        low, high = (Vector(value) for value in bounds)
        if (len(low) != 3 or len(high) != 3 or any(not math.isfinite(v) for p in (low, high) for v in p)
                or any(high[i] <= low[i] for i in range(3))):
            raise _error('Head bounds need finite armature-space minimum and maximum coordinates.')
        inverse = head_frame.inverted()
        points = [inverse @ Vector((x, y, z)) for x in (low.x, high.x)
                  for y in (low.y, high.y) for z in (low.z, high.z)]
    fitted = len(points) >= 8
    low, high = _bounds(points) if fitted else ([-.65*length, -.85*length, -.1*length], [.65*length, .65*length, 1.55*length])
    if any(high[i] - low[i] < length*.1 for i in range(3)):
        low, high, fitted = [-.65*length, -.85*length, -.1*length], [.65*length, .65*length, 1.55*length], False
    margin = length*.055
    low, high = [value-margin for value in low], [value+margin for value in high]
    neck_points = [p for p in _points(armature, body, neck.name, neck_frame, .25)
                   if .05*neck.length <= p.z <= .65*neck.length]
    if len(neck_points) >= 8:
        nlow, nhigh = _bounds(neck_points)
        center = ((nlow[0]+nhigh[0])*.5, (nlow[1]+nhigh[1])*.5, .35*neck.length)
        radius = ((nhigh[0]-nlow[0])*.56, (nhigh[1]-nlow[1])*.56)
    else:
        center, radius = (0, 0, .35*neck.length), ((high[0]-low[0])*.25, (high[1]-low[1])*.22)
    return {'body': body.name if body else '', 'method': 'BOUNDS' if bounds is not None else 'HEAD_WEIGHTS' if fitted else 'BONE',
            'point_count': len(points), 'bounds': [low, high],
            'head_frame': [list(row) for row in head_frame], 'neck_frame': [list(row) for row in neck_frame],
            'neck_center': list(center), 'neck_radius': list(radius)}


def _rounded_point(point, low, high, radius):
    """Round the side silhouette while retaining the frontal head profile."""
    point = Vector(point)
    end_distance = min(point.z-low[2], high[2]-point.z)
    if end_distance < radius:
        inset = radius-math.sqrt(max(0.0, radius*radius-(radius-end_distance)**2))
        center = (low[1]+high[1])*.5
        point.y += (center-point.y) * (2*inset/(high[1]-low[1]))
    return point


def _rounded_wire(vertices, edges, low, high, radius, segments=32):
    """Soften depth corners while retaining the simple wire connections."""
    points = [Vector(point) for point in vertices]
    result = [_rounded_point(p, low, high, radius) for p in points]
    links = []
    for a, b in edges:
        previous = a
        for step in range(1, segments):
            current = len(result)
            result.append(_rounded_point(points[a].lerp(points[b], step/segments), low, high, radius))
            links.append((previous,current))
            previous = current
        links.append((previous,b))
    return [tuple(point) for point in result], links


def _geometry(role, fit, *, rounded=True):
    if role == 'HEAD':
        low, high = fit['bounds']
        center, width, height = (low[0]+high[0])*.5, (high[0]-low[0])*.5, high[2]-low[2]
        outline = [(-.58, 0), (.58, 0), (.92, .18), (1, .40), (1, .88),
                   (.76, 1), (-.76, 1), (-1, .88), (-1, .40), (-.92, .18)]
        vertices = [(center+x*width, y, low[2]+z*height) for y in (low[1], high[1]) for x, z in outline]
        # No front chin bar or face crossbars: the opening is framed at its
        # outside edge, with a clipped crown and tapered lower side profiles.
        edges = [(i, (i+1) % 10) for i in range(1, 10)]
        edges += [(10+i, 10+(i+1) % 10) for i in range(10)]
        edges += [(i, i+10) for i in (2, 4, 5, 6, 7, 9)]
        if rounded:
            # Sweep the front/back profiles inward at the crown and chin so
            # depth rails meet a curved side silhouette. Keep X/Z proportions.
            radius = min(high[i]-low[i] for i in range(3)) * .13
            vertices, edges = _rounded_wire(vertices, edges, low, high, radius)
        start = len(vertices)
        marker = Vector((center,low[1],high[2]))
        if rounded:
            marker = _rounded_point(marker, low, high, radius)
        vertices += [tuple(marker + Vector(offset)) for offset in
                     ((0,0,0), (0,-width*.24,0), (-width*.08,-width*.12,0), (width*.08,-width*.12,0))]
        edges += [(start, start+1), (start+1, start+2), (start+1, start+3)]
    else:
        cx, cy, cz = fit['neck_center']
        rx, ry = fit['neck_radius']
        vertices = [(cx+rx*math.sin(angle), cy-ry*math.cos(angle), cz)
                    for angle in (math.radians(42+i*(276/24)) for i in range(25))]
        edges = [(i, i+1) for i in range(24)]
        vertices += [(vertices[i][0], vertices[i][1], cz+min(rx, ry)*.18) for i in (0, 24)]
        edges += [(0, 25), (24, 26)]
    return vertices, edges


def _create_widget(context, armature, record, name, entry):
    collection = bpy.data.collections.get(record['collection'])
    if collection is None:
        collection = bpy.data.collections.new(record['collection'])
        context.scene.collection.children.link(collection)
        _tag(collection, record, 'COLLECTION')
    mesh = bpy.data.meshes.new(entry['mesh'])
    _tag(mesh, record, entry['role'])
    vertices, edges = _geometry(entry['role'], record['fit'])
    frame = Matrix(record['fit']['head_frame' if entry['role'] == 'HEAD' else 'neck_frame'])
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
    for entry in record['bindings'].values():
        obj = bpy.data.objects.get(entry['object'])
        if _owned(obj, record, entry['role']):
            bpy.data.objects.remove(obj, do_unlink=True)
        mesh = bpy.data.meshes.get(entry['mesh'])
        if _owned(mesh, record, entry['role']) and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    collection = bpy.data.collections.get(record['collection'])
    if _owned(collection, record, 'COLLECTION') and not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)


def build(context, armature, *, head_name=None, neck_name=None, body_source=None, bounds=None):
    """Replace Head/Neck displays explicitly, preserving artist shapes and colors.

    Optional bounds are an armature-space (minimum, maximum) head box.
    Existing native pose animation is allowed; animated display channels are not.
    """
    _active(context, armature)
    if previous := validate(armature):
        return previous
    head_name, neck_name = resolve_bones(context, armature, head_name, neck_name)
    if any(visuals._animated_display(armature, armature.pose.bones[name]) for name in (head_name, neck_name)):
        raise _error('Head/Neck custom-shape display has animation or drivers; preserve those channels first.')
    fit = _fit(context, armature, armature.data.bones[head_name], armature.data.bones[neck_name], body_source, bounds)
    record = {'version': VERSION, 'id': uuid.uuid4().hex, 'head': head_name, 'neck': neck_name, 'bindings': {}, 'fit': fit}
    record['collection'] = 'CD_Head_Neck_Widgets_' + record['id'][:10]
    before, refs = {}, {}
    for role, name in (('HEAD', head_name), ('NECK', neck_name)):
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


def _refuse_dependencies(armature, record):
    collection = bpy.data.collections[record['collection']]
    objects = {entry['object']: name for name, entry in record['bindings'].items()}
    if collection.children or collection.users > 1 or set(collection.objects.keys()) != set(objects):
        raise _error('The Head/Neck widget collection is shared or contains artist data; separate it before removal.')
    for entry in record['bindings'].values():
        obj = bpy.data.objects[entry['object']]
        if (obj.data.users != 1 or obj.users > 2 or tuple(obj.users_collection) != (collection,)
                or obj.animation_data is not None or obj.data.animation_data is not None or obj.data.shape_keys is not None):
            raise _error('A Head/Neck widget is shared or animated; preserve that use before removal.')
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            for pb in obj.pose.bones:
                if pb.custom_shape and pb.custom_shape.name in objects and (obj != armature or pb.name != objects[pb.custom_shape.name]):
                    raise _error('Another bone uses a Head/Neck widget; preserve that use before removal.')
    if any(visuals._animated_display(armature, armature.pose.bones[name]) for name in record['bindings']):
        raise _error('Head/Neck custom-shape display has animation or drivers; preserve those channels before removal.')


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
