"""Fit managed Foot/Master outlines to world Z=0 through display settings only.

The caller owns character placement and its root-transform rollback. This
module neither moves a functional bone nor rewrites visual defaults/provenance.
"""
import hashlib
import json
import math

import bpy
from mathutils import Euler, Matrix, Vector

from . import control_colors, limb_ik, limb_fk_visuals, root_control


FLOOR_TOLERANCE = 1e-5
_POSE_FIELDS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')


def _pointer(value):
    try:
        return value.as_pointer() if value is not None else None
    except ReferenceError:
        return None


def _plain(value):
    if isinstance(value, bpy.types.ID):
        return ('ID', _pointer(value))
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, 'items'):
        return {key: _plain(item) for key, item in value.items()}
    return tuple(_plain(item) for item in value)


def _geometry(mesh):
    # Never edit or normalize shared/user widget data. Include attributes and
    # material bindings so a successful fit cannot hide an accidental rewrite.
    attributes = []
    for attribute in mesh.attributes:
        values = []
        for item in attribute.data:
            values.append({prop.identifier: _plain(getattr(item, prop.identifier))
                for prop in item.bl_rna.properties if prop.identifier != 'rna_type'
                and not prop.is_readonly and prop.type != 'COLLECTION'})
        attributes.append((attribute.name, attribute.data_type, attribute.domain, values))
    state = (tuple(tuple(vertex.co) for vertex in mesh.vertices),
        tuple(tuple(edge.vertices) for edge in mesh.edges),
        tuple(tuple(face.vertices) for face in mesh.polygons), attributes,
        tuple(_pointer(material) for material in mesh.materials))
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()


def _protected(pb, *, allow_rotation=False):
    bone = pb.bone
    return {'bone_pointer': _pointer(bone), 'pose_pointer': _pointer(pb),
        'widget': _pointer(pb.custom_shape), 'mesh': _pointer(pb.custom_shape.data),
        'transform': _pointer(pb.custom_shape_transform),
        'rest': (tuple(bone.head_local), tuple(bone.tail_local),
            tuple(tuple(row) for row in bone.matrix_local), _pointer(bone.parent),
            bone.use_connect, bone.use_deform),
        'pose': (pb.rotation_mode, tuple((field, tuple(getattr(pb, field))) for field in _POSE_FIELDS),
                 tuple(tuple(row) for row in pb.matrix_basis)),
        'display': (tuple(pb.custom_shape_scale_xyz),
            None if allow_rotation else tuple(pb.custom_shape_rotation_euler),
            pb.use_custom_shape_bone_size, getattr(pb, 'custom_shape_wire_width', None),
            getattr(pb, 'use_transform_at_custom_shape', None),
            getattr(pb, 'use_transform_around_custom_shape', None)),
        'colors': control_colors.capture_bone(pb),
        'bone_properties': _plain(dict(bone.items())), 'pose_properties': _plain(dict(pb.items())),
        'geometry': _geometry(pb.custom_shape.data)}


def _entries(state):
    rig = state['rig']
    if (_pointer(rig) != state['rig_pointer'] or _pointer(rig.data) != state['data_pointer']):
        raise ValueError('The original character rig disappeared during floor-outline fitting.')
    for entry in state['entries']:
        pb = rig.pose.bones.get(entry['name'])
        if pb is None or _pointer(pb) != entry['protected']['pose_pointer']:
            raise ValueError('A selected floor-outline control disappeared during fitting.')
        if pb.custom_shape is None or pb.custom_shape.type != 'MESH':
            raise ValueError('A selected floor-outline widget was replaced during fitting.')
        yield entry, pb


def _unchanged(state):
    for entry, pb in _entries(state):
        if _protected(pb, allow_rotation=state['align_planes']) != entry['protected']:
            raise ValueError(f"Floor-outline fitting changed protected inputs on '{pb.name}'.")


def _display(context, state, pb, *, rotation=None):
    rig = state['rig']
    evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
    anchor_name = pb.custom_shape_transform.name if pb.custom_shape_transform else pb.name
    anchor = (rig.data.bones[anchor_name].matrix_local if rig.data.pose_position == 'REST'
              else evaluated.pose.bones[anchor_name].matrix)
    anchor_world = evaluated.matrix_world @ anchor
    anchor_determinant = anchor_world.to_3x3().determinant()
    if (not all(math.isfinite(value) for row in anchor_world for value in row)
            or not math.isfinite(anchor_determinant) or abs(anchor_determinant) <= 1e-12):
        raise ValueError(f"Floor outline '{pb.name}' has a singular or non-finite display frame.")
    size = pb.bone.length if pb.use_custom_shape_bone_size else 1.
    scale = Vector(pb.custom_shape_scale_xyz) * size
    rotation = pb.custom_shape_rotation_euler if rotation is None else rotation
    values = (*pb.custom_shape_translation, *rotation, *scale)
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"Floor outline '{pb.name}' has non-finite visual settings.")
    display = anchor_world @ Matrix.LocRotScale(pb.custom_shape_translation,
        rotation.to_quaternion(), scale)
    display_determinant = display.to_3x3().determinant()
    if not math.isfinite(display_determinant) or abs(display_determinant) <= 1e-12:
        raise ValueError(f"Floor outline '{pb.name}' has a singular or non-finite display frame.")
    points = [display @ vertex.co for vertex in pb.custom_shape.data.vertices]
    if not points or not all(math.isfinite(value) for point in points for value in point):
        raise ValueError(f"Floor outline '{pb.name}' needs finite, nonempty widget geometry.")
    return anchor_world, points


def _plane(pb):
    """Find a plane in scaled widget coordinates without rewriting geometry.

    A genuinely nonplanar artist shape keeps its orientation. Very small
    native rounding of an otherwise planar mesh is accepted; final world
    vertex verification still uses the unchanged absolute floor tolerance.
    """
    size = pb.bone.length if pb.use_custom_shape_bone_size else 1.
    scale = Vector(pb.custom_shape_scale_xyz) * size
    points = [Vector(tuple(vertex.co[index] * scale[index] for index in range(3)))
              for vertex in pb.custom_shape.data.vertices]
    if len(points) < 3:
        return None
    edges = [point - points[0] for point in points[1:]]
    heading = next((edge.normalized() for edge in edges if edge.length > 1e-10), None)
    if heading is None:
        return None
    normal = max((heading.cross(edge) for edge in edges), key=lambda value: value.length_squared)
    if normal.length <= 1e-10:
        return None
    normal.normalize()
    tolerance = 1e-6 * max(1., max(edge.length for edge in edges))
    if max(abs(normal.dot(point - points[0])) for point in points) > tolerance:
        return None
    return {'heading': tuple(heading), 'normal': tuple(normal)}


def _plan_rotation(anchor, pb, plane, points):
    """Horizontalize a plane while keeping its projected in-plane heading.

    A includes world/object and custom anchor scale/shear. Its transpose maps
    the world plane normal into the anchor's local plane equation. Keeping
    the old projected heading fixes the remaining in-plane rotation, without
    decomposing A or changing either functional bones or custom-shape scale.
    """
    old = pb.custom_shape_rotation_euler.copy()
    if plane is None or max(point.z for point in points) - min(point.z for point in points) <= FLOOR_TOLERANCE:
        return old
    linear = anchor.to_3x3()
    source_heading, source_normal = Vector(plane['heading']), Vector(plane['normal'])
    old_rotation = old.to_matrix()
    target_normal = (linear.transposed() @ Vector((0., 0., 1.))).normalized()
    if target_normal.dot(old_rotation @ source_normal) < 0.:
        target_normal.negate()
    heading = linear @ (old_rotation @ source_heading)
    heading.z = 0.
    # An edge viewed exactly vertically has no horizontal direction. The
    # independent second in-plane axis then supplies a stable heading.
    if heading.length <= 1e-10:
        source_heading = source_normal.cross(source_heading).normalized()
        heading = linear @ (old_rotation @ source_heading)
        heading.z = 0.
    if heading.length <= 1e-10:
        raise ValueError(f"Floor outline '{pb.name}' has no finite projected heading.")
    target_heading = linear.inverted() @ heading
    target_heading -= target_normal * target_heading.dot(target_normal)
    target_heading.normalize()
    source_basis = Matrix((source_heading, source_normal.cross(source_heading), source_normal)).transposed()
    target_basis = Matrix((target_heading, target_normal.cross(target_heading), target_normal)).transposed()
    rotation = (target_basis @ source_basis.transposed()).to_euler('XYZ', old)
    if not all(math.isfinite(value) for value in rotation):
        raise ValueError(f"Floor outline '{pb.name}' cannot derive a finite display rotation.")
    return rotation


def prepare(context, rig, *, align_planes=False):
    """Validate all selected displays before the caller moves the character."""
    inventory = limb_ik._validate_inventory(rig)
    root = root_control.validate(rig, inventory=inventory)
    selected = []
    for key, data in inventory['rigs'].items():
        if key[0] == 'LEG':
            selected.append(rig.pose.bones[data['target'].name])
    master = root['master'] if root is not None else (
        inventory['master'].name if inventory.get('master') is not None else None)
    if master:
        selected.append(rig.pose.bones[master])
    state = {'rig': rig, 'rig_pointer': _pointer(rig), 'data_pointer': _pointer(rig.data),
             'align_planes': bool(align_planes), 'entries': []}
    for pb in selected:
        if pb.custom_shape is None or pb.custom_shape.type != 'MESH':
            raise ValueError(f"Managed floor control '{pb.name}' needs its Mesh custom shape.")
        widget = pb.custom_shape
        if (widget.modifiers or widget.data.shape_keys is not None
                or getattr(widget.data, 'animation_data', None) is not None):
            raise ValueError(f"Floor outline '{pb.name}' has modified, keyed or animated widget geometry; preserve that display first.")
        if pb.custom_shape_transform is not None and pb.custom_shape_transform.id_data is not rig:
            raise ValueError(f"Floor outline '{pb.name}' has an external display transform.")
        if limb_fk_visuals._animated_display(rig, pb):
            raise ValueError(f"Floor outline '{pb.name}' has animated or driven display settings; preserve that animation first.")
        anchor, points = _display(context, state, pb)
        plane = _plane(pb) if align_planes else None
        _plan_rotation(anchor, pb, plane, points)
        state['entries'].append({'name': pb.name, 'translation': tuple(pb.custom_shape_translation),
            'rotation': tuple(pb.custom_shape_rotation_euler), 'plane': plane,
            'protected': _protected(pb, allow_rotation=state['align_planes'])})
    return state


def verify(context, state):
    """Prove planar displays or nonplanar bottoms reach zero, protecting inputs."""
    context.view_layer.update()
    _unchanged(state)
    controls = {}
    for entry, pb in _entries(state):
        _anchor, points = _display(context, state, pb)
        minimum, maximum = min(point.z for point in points), max(point.z for point in points)
        if abs(minimum) > FLOOR_TOLERANCE:
            raise ValueError(f"Floor outline '{pb.name}' did not reach world Z=0.")
        if entry['plane'] is not None and max(abs(minimum), abs(maximum)) > FLOOR_TOLERANCE:
            raise ValueError(f"Planar floor outline '{pb.name}' did not become horizontal at world Z=0.")
        if entry['plane'] is None and tuple(pb.custom_shape_rotation_euler) != entry['rotation']:
            raise ValueError(f"Nonplanar floor outline '{pb.name}' changed its original display rotation.")
        controls[pb.name] = {'minimum_world_z': minimum, 'maximum_world_z': maximum,
            'z_span': maximum-minimum, 'translation_before': list(entry['translation']),
            'translation_after': list(pb.custom_shape_translation),
            'rotation_before': list(entry['rotation']), 'rotation_after': list(pb.custom_shape_rotation_euler),
            'planar_alignment': entry['plane'] is not None,
            'all_vertices_on_floor': max(abs(minimum), abs(maximum)) <= FLOOR_TOLERANCE,
            'protected_unchanged': True}
    return {'count': len(controls), 'controls': controls, 'world_floor': 0.,
            'tolerance': FLOOR_TOLERANCE, 'align_planes': state['align_planes'],
            'changed_fields_only': ['custom_shape_translation'] + (
                ['custom_shape_rotation_euler'] if state['align_planes'] else [])}


def restore(context, state):
    """Restore exact original live offsets/rotation, leaving defaults untouched."""
    entries = list(_entries(state))
    for entry, pb in entries:
        if tuple(pb.custom_shape_translation) != entry['translation']:
            pb.custom_shape_translation = entry['translation']
        if tuple(pb.custom_shape_rotation_euler) != entry['rotation']:
            pb.custom_shape_rotation_euler = entry['rotation']
    context.view_layer.update()
    _unchanged(state)
    if any(tuple(pb.custom_shape_translation) != entry['translation']
           or tuple(pb.custom_shape_rotation_euler) != entry['rotation'] for entry, pb in entries):
        raise RuntimeError('Floor-outline display settings could not be restored exactly.')


def apply(context, state):
    """Fit current evaluated display bottoms; caller rolls back root movement."""
    context.view_layer.update()
    _unchanged(state)
    planned = []
    for entry, pb in _entries(state):
        anchor, points = _display(context, state, pb)
        rotation = _plan_rotation(anchor, pb, entry['plane'], points)
        anchor, points = _display(context, state, pb, rotation=rotation)
        delta = anchor.to_3x3().inverted() @ Vector((0., 0., -min(point.z for point in points)))
        translation = Vector(pb.custom_shape_translation) + delta
        if not all(math.isfinite(value) for value in translation):
            raise ValueError(f"Floor outline '{pb.name}' needs a non-finite visual offset; no changes kept.")
        planned.append((pb, translation, rotation))
    try:
        for pb, translation, rotation in planned:
            if tuple(pb.custom_shape_rotation_euler) != tuple(rotation):
                pb.custom_shape_rotation_euler = rotation
            pb.custom_shape_translation = translation
        return verify(context, state)
    except Exception as original:
        try:
            restore(context, state)
        except Exception as recovery:
            raise RuntimeError(f'Floor-outline fitting failed: {original}. Offset recovery also failed: {recovery}.') from original
        raise
