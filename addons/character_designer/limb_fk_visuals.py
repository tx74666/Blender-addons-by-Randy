"""Reversible native FK rings; no bones, constraints or pose channels are added."""
from __future__ import annotations

import json
import math
import uuid

import bpy

OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'limb_fk_visuals'
ID_KEY = 'character_designer_fk_visual_id'
ROLE_KEY = 'character_designer_fk_visual_role'
RIG_KEY = 'character_designer_fk_visual_armature_id'
RECORD_KEY = 'character_designer_limb_fk_visuals_v1'
IK_SIZE_RECORD_KEY = 'character_designer_limb_ik_size_fit_v1'
VERSION = 1
RING_SEGMENTS = 32
RING_FLATTENING = .819
RADIUS_FACTORS = {'ARM': .15, 'LEG': .22}
_DISPLAY_PATHS = ('custom_shape', 'custom_shape_transform', 'custom_shape_scale_xyz',
                  'custom_shape_translation', 'custom_shape_rotation_euler',
                  'use_custom_shape_bone_size', 'custom_shape_wire_width')


def _limb():
    from . import limb_ik
    return limb_ik


def _error(message):
    return _limb().LimbIKError(message)


def _active(context, armature):
    from . import torso_controls
    torso_controls._active(context, armature)


def _update(context, armature):
    from . import torso_controls
    torso_controls._update(context, armature)


def _tag(item, record, role):
    item[OWNER_KEY], item[ID_KEY] = OWNER_VALUE, record['id']
    item[ROLE_KEY], item[RIG_KEY] = role, record['armature_id']


def _owned(item, record, role):
    return (item is not None and item.get(OWNER_KEY) == OWNER_VALUE
            and item.get(ID_KEY) == record['id'] and item.get(ROLE_KEY) == role
            and item.get(RIG_KEY) == record['armature_id'])


def get_record(armature):
    raw = armature.data.get(RECORD_KEY)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        if (record['version'] != VERSION or not isinstance(record['bindings'], dict)
                or not record['bindings'] or not isinstance(record['id'], str)
                or record['armature_id'] != armature.data.get(_limb().ARMATURE_ID_KEY)):
            raise ValueError('unsupported record')
        for name, entry in record['bindings'].items():
            if (entry['kind'] not in RADIUS_FACTORS or entry['side'] not in {'L', 'R'}
                    or len(entry['chain']) != 3 or name not in entry['chain'][:2]
                    or not isinstance(entry['object'], str) or not isinstance(entry['mesh'], str)):
                raise ValueError('invalid binding')
            _limb()._validate_shape_state(entry['original'], 'Saved FK ring display', original=True)
            _limb()._validate_shape_state(entry['generated'], 'Generated FK ring display')
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error('FK ring recovery data is invalid; restore a saved copy.') from exc


def _same_display(pb, expected):
    actual = _limb()._pose_shape_json_state(pb)
    return (actual['custom_shape_transform'] == expected['custom_shape_transform']
            and actual['use_bone_size'] == expected['use_bone_size']
            and all(_limb()._shape_values_match(actual[key], expected[key])
                    for key in ('scale', 'translation', 'rotation'))
            and (expected['wire_width'] is None or abs(actual['wire_width'] - expected['wire_width']) < 1e-6))


def validate(armature, inventory=None):
    """Validate this visual registry without recursively entering limb inventory."""
    record = get_record(armature)
    rig_id = armature.data.get(_limb().ARMATURE_ID_KEY)
    owned_objects = {obj.name for obj in bpy.data.objects
                     if obj.get(OWNER_KEY) == OWNER_VALUE and obj.get(RIG_KEY) == rig_id}
    if record is None:
        if owned_objects:
            raise _error('FK ring widgets have lost their recovery data.')
        return None
    try:
        if owned_objects != {entry['object'] for entry in record['bindings'].values()}:
            raise _error('FK ring widgets were added, removed or renamed.')
        collection = bpy.data.collections.get(record['collection'])
        if not _owned(collection, record, 'COLLECTION'):
            raise _error('The FK ring widget collection is missing or was replaced.')
        for name, entry in record['bindings'].items():
            pb = armature.pose.bones.get(name)
            obj = bpy.data.objects.get(entry['object'])
            if (pb is None or pb.bone.get(OWNER_KEY) in _limb().GENERATED_CONTROL_OWNERS
                    or not _owned(obj, record, name) or obj.type != 'MESH'
                    or obj.data.name != entry['mesh'] or not _owned(obj.data, record, name)
                    or pb.custom_shape != obj or not _same_display(pb, entry['generated'])):
                raise _error(f"FK ring assignment or display on '{name}' was edited; preserve that setup first.")
            if inventory is not None:
                rig = inventory['rigs'].get((entry['kind'], entry['side']))
                if rig is None or list(rig['chain']) != entry['chain']:
                    raise _error(f"The limb using FK ring '{name}' changed; remove its ring visuals before rebuilding.")
    except (KeyError, TypeError, AttributeError) as exc:
        raise _error('FK ring recovery data no longer matches this armature.') from exc
    return record


def _animated_display(armature, pb):
    paths = {pb.path_from_id(field) for field in _DISPLAY_PATHS if hasattr(pb, field)}
    curves = [curve for action in _limb()._actions_for_id(armature)
              for curve in _limb()._fcurves_for_action(action)]
    if armature.animation_data:
        curves.extend(armature.animation_data.drivers)
    return any(curve.data_path in paths for curve in curves)


def _geometry():
    vertices = [(math.cos(i * math.tau / RING_SEGMENTS), 0.0,
                 math.sin(i * math.tau / RING_SEGMENTS) * RING_FLATTENING)
                for i in range(RING_SEGMENTS)]
    return vertices, [(i, (i + 1) % RING_SEGMENTS) for i in range(RING_SEGMENTS)]


def _create_widget(context, armature, record, name, entry):
    collection = bpy.data.collections.get(record['collection'])
    if collection is None:
        collection = bpy.data.collections.new(record['collection'])
        context.scene.collection.children.link(collection)
        _tag(collection, record, 'COLLECTION')
    mesh = bpy.data.meshes.new(entry['mesh'])
    entry['mesh'] = mesh.name
    _tag(mesh, record, name)
    vertices, edges = _geometry()
    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    obj = bpy.data.objects.new(entry['object'], mesh)
    entry['object'] = obj.name
    _tag(obj, record, name)
    collection.objects.link(obj)
    obj.hide_render, obj.hide_select = True, True
    obj.hide_set(True)
    pb = armature.pose.bones[name]
    pb.custom_shape = obj
    # No custom-shape transform or midpoint offset: the ring follows the
    # native FK joint itself, including an existing intermediate IK/FK value.
    _limb()._apply_source_widget_generated_state(pb, entry['generated'])


def _delete_resources(record, names):
    for name in names:
        entry = record['bindings'][name]
        obj = bpy.data.objects.get(entry['object'])
        if _owned(obj, record, name):
            bpy.data.objects.remove(obj, do_unlink=True)
        mesh = bpy.data.meshes.get(entry['mesh'])
        if _owned(mesh, record, name) and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    collection = bpy.data.collections.get(record['collection'])
    if collection is not None and not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)


def _verify_pose(armature, expected):
    for name, matrix in expected.items():
        for i in range(4):
            for j in range(4):
                error = abs(armature.pose.bones[name].matrix[i][j] - matrix[i][j])
                if not math.isfinite(error) or error > 2e-6:
                    raise _error('FK ring visuals could not preserve the current pose; no changes kept.')


def build(context, armature):
    """Add missing upper/lower rings; keep existing artist shapes and anchors."""
    _active(context, armature)
    inventory = _limb()._validate_inventory(armature)
    previous = validate(armature, inventory)
    before_raw = armature.data.get(RECORD_KEY)
    record = json.loads(before_raw) if previous else {
        'version': VERSION, 'id': uuid.uuid4().hex, 'armature_id': inventory['armature_id'],
        'bindings': {}, 'skipped': {},
    }
    record.setdefault('collection', 'CD_FK_Ring_Widgets_' + record['id'][:10])
    pending = []
    originals = {}
    for (kind, side), rig in inventory['rigs'].items():
        for name in rig['chain'][:2]:
            if name in record['bindings']:
                continue
            pb = armature.pose.bones[name]
            if pb.custom_shape is not None or pb.custom_shape_transform is not None:
                record['skipped'][name] = 'Existing artist shape or custom-shape transform'
                continue
            if _animated_display(armature, pb):
                record['skipped'][name] = 'Animated custom-shape display'
                continue
            radius = float(pb.bone.length) * RADIUS_FACTORS[kind]
            if not math.isfinite(radius) or radius <= 1e-6:
                raise _error(f"Bone '{name}' is too short for an FK ring.")
            original = _limb()._pose_shape_json_state(pb)
            generated = {'custom_shape': '', 'custom_shape_transform': '', 'use_bone_size': False,
                         'scale': [radius] * 3, 'translation': [0.0] * 3, 'rotation': [0.0] * 3,
                         'wire_width': 2.0 if hasattr(pb, 'custom_shape_wire_width') else None}
            widget_name = 'WGT_CD_FK_' + name + '_' + record['id'][:10]
            record['bindings'][name] = {'kind': kind, 'side': side, 'chain': list(rig['chain']),
                                         'object': widget_name, 'mesh': widget_name,
                                         'original': original, 'generated': generated}
            originals[name] = _limb()._pose_shape_runtime_state(pb)
            record['skipped'].pop(name, None)
            pending.append(name)
    if not pending:
        return previous or {'version': VERSION, 'bindings': {}, 'skipped': record['skipped']}
    _update(context, armature)
    expected = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    try:
        for name in pending:
            _create_widget(context, armature, record, name, record['bindings'][name])
        armature.data[RECORD_KEY] = json.dumps(record)
        _update(context, armature)
        _verify_pose(armature, expected)
        validate(armature, inventory)
    except Exception:
        for name, state in originals.items():
            _limb()._restore_pose_shape_state(armature, armature.pose.bones[name], state, runtime=True)
        _delete_resources(record, pending)
        if before_raw is None:
            armature.data.pop(RECORD_KEY, None)
        else:
            armature.data[RECORD_KEY] = before_raw
        _update(context, armature)
        raise
    return record


apply = build


def _refuse_dependencies(armature, record):
    collection = bpy.data.collections[record['collection']]
    objects = {entry['object']: name for name, entry in record['bindings'].items()}
    if (collection.children or collection.users > 1
            or set(collection.objects.keys()) != set(objects)):
        raise _error('The FK ring widget collection contains artist data or is shared; separate it before removal.')
    for entry in record['bindings'].values():
        obj = bpy.data.objects[entry['object']]
        if (obj.data.users != 1 or obj.users > 2 or tuple(obj.users_collection) != (collection,)
                or obj.animation_data is not None or obj.data.animation_data is not None):
            raise _error('An FK ring widget is shared or animated; preserve that use before removal.')
    for obj in bpy.data.objects:
        if obj.type != 'ARMATURE':
            continue
        for pb in obj.pose.bones:
            if (pb.custom_shape and pb.custom_shape.name in objects
                    and (obj != armature or pb.name != objects[pb.custom_shape.name])):
                raise _error('An FK ring widget is used by another bone; preserve that use before removal.')
    for name in record['bindings']:
        if _animated_display(armature, armature.pose.bones[name]):
            raise _error('An FK ring display has animation or drivers; preserve those channels before removal.')


def remove(context, armature):
    """Restore the exact preceding display state and delete only owned widgets."""
    from . import control_colors
    _active(context, armature)
    record = validate(armature)
    if record is None:
        return {'removed': 0}
    _refuse_dependencies(armature, record)
    _update(context, armature)
    expected = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    before = {name: _limb()._pose_shape_runtime_state(armature.pose.bones[name]) for name in record['bindings']}
    try:
        for name, entry in record['bindings'].items():
            _limb()._restore_pose_shape_state(armature, armature.pose.bones[name], entry['original'])
        _update(context, armature)
        _verify_pose(armature, expected)
    except Exception:
        for name, state in before.items():
            _limb()._restore_pose_shape_state(armature, armature.pose.bones[name], state, runtime=True)
        _update(context, armature)
        raise
    _delete_resources(record, record['bindings'])
    del armature.data[RECORD_KEY]
    control_colors.cleanup(armature)
    return {'removed': len(record['bindings'])}


def has_ik_size_backup(armature):
    return bool(armature and armature.type == 'ARMATURE' and IK_SIZE_RECORD_KEY in armature.data)


def _size_record(armature):
    raw = armature.data.get(IK_SIZE_RECORD_KEY)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        if (record['version'] != VERSION or not isinstance(record['bindings'], dict)
                or not record['bindings']
                or record['armature_id'] != armature.data.get(_limb().ARMATURE_ID_KEY)):
            raise ValueError('unsupported structure')
        for name, entry in record['bindings'].items():
            if (not isinstance(name, str) or not isinstance(entry['object'], str)
                    or not isinstance(entry['transform'], str)
                    or entry['factor'] not in {.8, .55}):
                raise ValueError('invalid size binding')
            for key in ('original', 'fitted'):
                _limb()._shape_vector({'scale': entry[key]}, 'scale', 'Saved IK control size')
        return record
    except (KeyError, TypeError, ValueError) as exc:
        raise _error('IK control-size recovery data is invalid; restore a saved copy.') from exc


def _validate_size_binding(armature, record, name, entry):
    pb = armature.pose.bones.get(name)
    if (pb is None or pb.bone.get(_limb().OWNER_KEY) != _limb().OWNER_VALUE
            or pb.bone.get(_limb().ARMATURE_ID_KEY) != record['armature_id']
            or pb.custom_shape is None or pb.custom_shape.name != entry['object']
            or (pb.custom_shape_transform.name if pb.custom_shape_transform else '') != entry['transform']
            or _limb()._control_shape_style(pb.bone) != 'DEFAULT'
            or not _limb()._shape_values_match(pb.custom_shape_scale_xyz, entry['fitted'])):
        raise _error(f"Fitted IK control '{name}' was edited; restore its fitted size and default shape before restoring its previous size.")
    if _animated_display(armature, pb):
        raise _error(f"Fitted IK control '{name}' has animated display settings; preserve those channels first.")
    return pb


def fit_ik_sizes(context, armature):
    """Explicitly shrink only default hand shapes and knee poles, with backups.

    The core's saved visual defaults remain unchanged. Translation, rotation,
    Custom Shape Transform and every pose channel remain untouched.
    """
    _active(context, armature)
    inventory = _limb()._validate_inventory(armature)
    previous = _size_record(armature)
    record = json.loads(json.dumps(previous)) if previous else {
        'version': VERSION, 'armature_id': inventory['armature_id'], 'bindings': {},
    }
    for name, entry in record['bindings'].items():
        _validate_size_binding(armature, record, name, entry)
    pending, skipped = {}, {}
    for (kind, side), rig in inventory['rigs'].items():
        name, factor = (rig['target'].name, .8) if kind == 'ARM' else (rig['pole'].name, .55)
        if name in record['bindings']:
            continue
        pb = armature.pose.bones[name]
        if _limb()._control_shape_style(pb.bone) != 'DEFAULT':
            skipped[name] = 'A custom shape style is selected'
            continue
        raw = pb.bone.get(_limb().CONTROL_VISUAL_DEFAULT_KEY)
        if raw is None:
            skipped[name] = 'No saved default-size evidence'
            continue
        default = _limb()._parse_control_visual_state(raw, f"Control '{name}'")
        if not _limb()._shape_values_match(pb.custom_shape_scale_xyz, default['scale']):
            skipped[name] = 'Artist-edited custom-shape size'
            continue
        if _animated_display(armature, pb):
            skipped[name] = 'Animated custom-shape display'
            continue
        original = list(pb.custom_shape_scale_xyz)
        pending[name] = {'original': original, 'fitted': [value * factor for value in original],
                         'object': pb.custom_shape.name, 'factor': factor,
                         'transform': pb.custom_shape_transform.name if pb.custom_shape_transform else ''}
    if not pending:
        return {'fitted': 0, 'skipped': skipped, 'retained': len(record['bindings'])}
    _update(context, armature)
    expected = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    before_raw = armature.data.get(IK_SIZE_RECORD_KEY)
    try:
        for name, entry in pending.items():
            armature.pose.bones[name].custom_shape_scale_xyz = entry['fitted']
        record['bindings'].update(pending)
        armature.data[IK_SIZE_RECORD_KEY] = json.dumps(record)
        _update(context, armature)
        _verify_pose(armature, expected)
        for name, entry in record['bindings'].items():
            _validate_size_binding(armature, record, name, entry)
    except Exception:
        for name, entry in pending.items():
            armature.pose.bones[name].custom_shape_scale_xyz = entry['original']
        if before_raw is None:
            armature.data.pop(IK_SIZE_RECORD_KEY, None)
        else:
            armature.data[IK_SIZE_RECORD_KEY] = before_raw
        _update(context, armature)
        raise
    return {'fitted': len(pending), 'skipped': skipped, 'retained': len(record['bindings']) - len(pending)}


def restore_ik_sizes(context, armature):
    """Restore only unchanged, recorded size fits; never overwrite artist edits."""
    _active(context, armature)
    record = _size_record(armature)
    if record is None:
        return {'restored': 0}
    for name, entry in record['bindings'].items():
        _validate_size_binding(armature, record, name, entry)
    _update(context, armature)
    expected = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    try:
        for name, entry in record['bindings'].items():
            armature.pose.bones[name].custom_shape_scale_xyz = entry['original']
        _update(context, armature)
        _verify_pose(armature, expected)
    except Exception:
        for name, entry in record['bindings'].items():
            armature.pose.bones[name].custom_shape_scale_xyz = entry['fitted']
        _update(context, armature)
        raise
    del armature.data[IK_SIZE_RECORD_KEY]
    return {'restored': len(record['bindings'])}
