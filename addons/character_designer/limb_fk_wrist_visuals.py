"""Explicit, reversible standard Hand silhouettes on native FK wrist bones.

The IK mesh is a read-only reference. FK widgets keep their own IDs and native
hand channels; frame following never creates or migrates widget geometry.
"""
from __future__ import annotations

import json
import math

import bpy

SHAPE_KIND = 'HAND'
GEOMETRY_TOLERANCE = 1e-6


class UnsupportedSource(RuntimeError):
    """A nonstandard IK display is retained instead of guessed or overwritten."""


def _visuals():
    from . import limb_fk_visuals
    return limb_fk_visuals


def _limb():
    return _visuals()._limb()


def geometry():
    """Use the same enhanced 32-point saddle as the ordinary Hand IK widget."""
    return _limb()._widget_geometry('HAND')


def _mesh_state(mesh):
    return {'vertices': [tuple(float(v) for v in vertex.co) for vertex in mesh.vertices],
            'edges': [tuple(int(v) for v in edge.vertices) for edge in mesh.edges]}


def _same_geometry(mesh, expected):
    vertices, edges = expected
    if (len(mesh.vertices) != len(vertices) or len(mesh.edges) != len(edges)
            or len(mesh.polygons)):
        return False
    if not all(len(vertex.co) == 3 and all(math.isfinite(float(a))
               and abs(float(a) - float(b)) <= GEOMETRY_TOLERANCE
               for a, b in zip(vertex.co, point))
               for vertex, point in zip(mesh.vertices, vertices)):
        return False
    actual_edges = [tuple(sorted(int(v) for v in edge.vertices)) for edge in mesh.edges]
    return (len(set(actual_edges)) == len(edges)
            and set(actual_edges) == {tuple(sorted(edge)) for edge in edges})


def _replace_geometry(mesh, vertices, edges):
    # Circle and Hand use the same 32-point cycle. Preserve native topology,
    # custom attributes, material slots and mesh/vertex/edge selection data.
    actual = [tuple(sorted(int(v) for v in edge.vertices)) for edge in mesh.edges]
    if (len(mesh.vertices) != len(vertices) or len(mesh.edges) != len(edges)
            or len(mesh.polygons) or len(set(actual)) != len(edges)
            or set(actual) != {tuple(sorted(edge)) for edge in edges}):
        raise _visuals()._error('The wrist widget topology changed; retain that geometry.')
    for vertex, point in zip(mesh.vertices, vertices):
        vertex.co = point
    mesh.update()


def _plain_mesh_object(obj):
    return (obj is not None and obj.type == 'MESH' and not obj.library
            and not obj.data.library and not obj.modifiers and not obj.constraints
            and obj.animation_data is None and obj.data.animation_data is None
            and obj.data.shape_keys is None)


def plan(armature, rig):
    """Derive a supported standard IK silhouette in the native hand frame.

    Auto Align already expresses its visual vectors in that frame. Without
    Auto Align only a stable Rest orientation is used: an FK target's stale
    location or current pose must never become a new wrist display offset.
    """
    V, L = _visuals(), _limb()
    end = armature.pose.bones[rig['chain'][2]]
    source = armature.pose.bones[rig['target'].name]
    shape = source.custom_shape
    rig_id = armature.data.get(L.ARMATURE_ID_KEY)
    if (not L._owned(source.bone, rig_id, role='HAND_IK', rig_id=rig['rig_id'])
            or L._control_shape_style(source.bone) != 'DEFAULT'
            or not _plain_mesh_object(shape)
            or not L._owned(shape, rig_id, role='WIDGET')
            or shape.get(L.KIND_KEY) != 'HAND'
            or not L._owned(shape.data, rig_id, role='WIDGET_DATA')
            or shape.data.get(L.KIND_KEY) != 'HAND'
            or not _same_geometry(shape.data, geometry())):
        raise UnsupportedSource('The IK wrist uses a nonstandard Hand shape; retain that display.')
    if source.custom_shape_transform not in (None, end):
        raise UnsupportedSource('The IK wrist has a custom display frame; retain that display.')
    if source.use_custom_shape_bone_size or V._animated_display(armature, source):
        raise UnsupportedSource('The IK wrist has nonstandard or animated display settings; retain them.')
    raw = source.bone.get(L.CONTROL_VISUAL_DEFAULT_KEY)
    if raw is None:
        raise UnsupportedSource('The IK wrist has no saved standard display evidence.')
    default = L._parse_control_visual_state(raw, 'Standard IK wrist display')
    actual = L._control_visual_state(source)
    if any(not L._shape_values_match(actual[key], default[key])
           for key in ('translation', 'rotation')):
        raise UnsupportedSource('The IK wrist has artist display offsets; retain them.')
    scale = actual['scale']
    if not L._shape_values_match(scale, default['scale']):
        fitted = V._size_record(armature)
        entry = (fitted or {}).get('bindings', {}).get(source.name)
        if (entry is None or entry['factor'] != .8
                or not L._shape_values_match(entry['original'], default['scale'])):
            raise UnsupportedSource('The IK wrist has an artist display size; retain it.')
        V._validate_size_binding(armature, fitted, source.name, entry)
    if (len(scale) != 3 or any(not math.isfinite(float(v)) or float(v) <= 1e-6 for v in scale)
            or max(scale) - min(scale) > GEOMETRY_TOLERANCE):
        raise UnsupportedSource('The standard Hand display requires a finite uniform positive size.')
    wire = float(source.custom_shape_wire_width) if hasattr(source, 'custom_shape_wire_width') else None
    if wire is not None and abs(wire - 2.0) > GEOMETRY_TOLERANCE:
        raise UnsupportedSource('The IK wrist has an artist line width; retain it.')
    if source.custom_shape_transform == end:
        translation, rotation = actual['translation'], actual['rotation']
    else:
        if any(abs(float(v)) > GEOMETRY_TOLERANCE for v in default['translation']):
            raise UnsupportedSource('The IK wrist has an unsupported display offset outside Auto Align.')
        lower = armature.pose.bones[rig['chain'][1]]
        end_rotation = end.bone.matrix_local.to_3x3().normalized()
        lower_rotation = lower.bone.matrix_local.to_3x3().normalized()
        if (abs(float(end_rotation.determinant())) <= 1e-8
                or abs(float(lower_rotation.determinant())) <= 1e-8):
            raise UnsupportedSource('The wrist Rest display frame is singular.')
        rotation = list((end_rotation.inverted() @ lower_rotation).to_euler('XYZ'))
        translation = [0.0, 0.0, 0.0]
    result = {'custom_shape': '', 'custom_shape_transform': '', 'use_bone_size': False,
              'scale': list(scale), 'translation': list(translation), 'rotation': list(rotation),
              'wire_width': wire}
    L._validate_shape_state(result, 'Standard FK Hand display')
    return result


def _editable_widget(armature, record, name, entry):
    V = _visuals()
    obj = bpy.data.objects.get(entry['object'])
    pb = armature.pose.bones[name]
    leaf = bpy.data.collections.get(record['collection'])
    if (not _plain_mesh_object(obj) or not V._owned(obj, record, name)
            or not V._owned(obj.data, record, name)
            or obj.data.name != entry['mesh'] or obj.data.users != 1
            or obj.use_fake_user or obj.data.use_fake_user
            or obj.users != 1 + int(pb.custom_shape == obj)
            or tuple(obj.users_collection) != (leaf,)
            or not V._owned(leaf, record, 'COLLECTION')):
        raise V._error(f"The FK wrist widget '{name}' is shared or edited; retain that setup.")
    if V._animated_display(armature, pb):
        raise V._error(f"The FK wrist display '{name}' is animated; retain those channels.")
    return obj


def _legacy_display(armature, entry):
    V = _visuals()
    radius = V.ring_radius(armature, 'ARM', entry['chain'], entry['chain'][2])
    expected = {'custom_shape_transform': '', 'use_bone_size': False,
                'scale': [radius] * 3, 'translation': [0.0] * 3, 'rotation': [0.0] * 3,
                'wire_width': 2.0 if hasattr(armature.pose.bones[entry['chain'][2]], 'custom_shape_wire_width') else None}
    actual = entry['generated']
    return (actual.get('custom_shape', '') == ''
            and actual.get('custom_shape_transform') == expected['custom_shape_transform']
            and actual.get('use_bone_size') is False
            and actual.get('wire_width') == expected['wire_width']
            and all(_limb()._shape_values_match(actual[key], expected[key])
                    for key in ('scale', 'translation', 'rotation')))


def migrate(context, armature, *, inventory=None, keys=None):
    """Convert eligible existing circles once, with all writes rolled back together.

    Missing wrists belong to explicit build. Artist geometry/display edits are
    refused. A nonstandard IK source is reported as skipped without changing it.
    The original pre-widget display remains the removal/Original recovery point.
    """
    V, L = _visuals(), _limb()
    V._active(context, armature)
    if (armature.data.pose_position != 'POSE'
            or any(key in armature for key in ('character_designer_body_original_mode_v1',
                       'character_designer_base_pose_test_v1', 'character_designer_natural_pose_test_v1'))
            or 'character_designer_bone_display_view_v1' in armature.data):
        raise V._error('Return explicitly to ordinary Controls before changing wrist shapes.')
    inventory = inventory if inventory is not None else L._validate_inventory(armature)
    record = V.validate(armature, inventory)
    if record is None:
        return {'migrated': 0, 'retained': 0, 'skipped': {}}
    chosen = set(inventory['rigs']) if keys is None else set(keys)
    if not chosen <= set(inventory['rigs']):
        raise V._error('A requested wrist has no owned limb setup.')
    pending, skipped, retained = {}, {}, 0
    for name, entry in record['bindings'].items():
        key = (entry['kind'], entry['side'])
        if key not in chosen or entry['kind'] != 'ARM' or name != entry['chain'][2]:
            continue
        obj = _editable_widget(armature, record, name, entry)
        if entry.get('shape_kind') == SHAPE_KIND:
            if not _same_geometry(obj.data, geometry()):
                raise V._error(f"The FK Hand geometry on '{name}' was edited; retain it.")
            retained += 1
            continue
        if (entry.get('shape_kind') is not None or not _legacy_display(armature, entry)
                or not _same_geometry(obj.data, V._geometry())):
            raise V._error(f"The FK wrist circle on '{name}' was edited; retain it.")
        try:
            generated = plan(armature, inventory['rigs'][key])
        except UnsupportedSource as exc:
            skipped[name] = str(exc)
            continue
        pending[name] = (obj, generated, _mesh_state(obj.data), L._pose_shape_runtime_state(armature.pose.bones[name]))
    if not pending:
        return {'migrated': 0, 'retained': retained, 'skipped': skipped}
    before_raw = armature.data[V.RECORD_KEY]
    updated = json.loads(before_raw)
    try:
        for name, (obj, generated, _mesh, _state) in pending.items():
            _replace_geometry(obj.data, *geometry())
            L._apply_source_widget_generated_state(armature.pose.bones[name], generated)
            updated['bindings'][name]['generated'] = generated
            updated['bindings'][name]['shape_kind'] = SHAPE_KIND
        armature.data[V.RECORD_KEY] = json.dumps(updated)
        V.validate(armature, inventory)
        for name, (obj, _generated, _mesh, _state) in pending.items():
            if not _same_geometry(obj.data, geometry()):
                raise V._error(f"The FK Hand geometry on '{name}' could not be verified.")
    except BaseException:
        for name, (obj, _generated, mesh, state) in pending.items():
            # Restore only coordinates. Topology and its custom attributes were
            # never replaced, including on a fault after a partial coordinate write.
            if len(obj.data.vertices) != len(mesh['vertices']):
                raise V._error('The wrist topology changed during migration; retain it for recovery.')
            for vertex, point in zip(obj.data.vertices, mesh['vertices']):
                vertex.co = point
            obj.data.update()
            L._restore_pose_shape_state(armature, armature.pose.bones[name], state, runtime=True)
        armature.data[V.RECORD_KEY] = before_raw
        raise
    return {'migrated': len(pending), 'retained': retained, 'skipped': skipped}
