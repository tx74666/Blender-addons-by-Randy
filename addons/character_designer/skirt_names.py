"""Transactional removal of legacy UUIDs from owned Dress bone/Hook names."""
import json
import re

import bpy

from . import generated_names, skirt_rig as skirt


def _remap(value, mapping):
    """Exact bone selectors only; prose and arbitrary substrings stay intact."""
    if isinstance(value, dict):
        return {mapping.get(key, key): _remap(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, mapping) for item in value]
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        for old, new in mapping.items():
            for selector in ('pose.bones', 'bones'):
                value = value.replace(selector + '[' + json.dumps(old) + ']',
                                      selector + '[' + json.dumps(new) + ']')
        return value
    return value


def _bound(obj, rig):
    return obj.type == 'MESH' and (obj.parent == rig or any(
        modifier.type == 'ARMATURE' and modifier.object == rig for modifier in obj.modifiers))


def _local(item):
    return not item.library and not item.override_library and item.is_editable


def _preflight(source, record, rig, mapping):
    if not _local(source) or not _local(rig) or not _local(rig.data) or rig.data.users != 1:
        raise ValueError('Dress name cleanup requires a local, single-user armature.')
    if source.mode == 'EDIT' or rig.mode == 'EDIT':
        raise ValueError('Leave Edit Mode before cleaning Dress names.')
    for old, new in mapping.items():
        if len(new.encode('utf-8')) > 63 or (new in rig.data.bones and new not in mapping):
            raise ValueError('A readable Dress bone name is already in use: ' + new)
        bone = rig.data.bones[old]
        if skirt.is_shared(record) and (bone.get(skirt.OWNER_KEY) != record['owner']
                                        or bone.get(skirt.SOURCE_KEY) != source):
            raise ValueError('Dress bone ownership changed; no names were modified.')
    for obj in bpy.data.objects:
        if _bound(obj, rig):
            if not _local(obj):
                raise ValueError('A linked mesh uses this Dress armature; names were retained.')
            for old, new in mapping.items():
                if new in obj.vertex_groups and new != old:
                    raise ValueError('A bound mesh already has the destination weight group: '
                                     + obj.name + ' / ' + new)
        # Native rename updates these references too. Never write into a linked
        # object or an override through that indirect operation.
        if not _local(obj):
            constraints = list(obj.constraints)
            if obj.type == 'ARMATURE' and obj.pose:
                constraints.extend(c for pb in obj.pose.bones for c in pb.constraints)
            if (obj.parent == rig or any(getattr(c, 'target', None) == rig for c in constraints)
                    or any(getattr(m, 'object', None) == rig for m in obj.modifiers)):
                raise ValueError('A linked object references this armature; names were retained.')
    actions = set()
    if rig.animation_data:
        if rig.animation_data.action:
            actions.add(rig.animation_data.action)
        actions.update(strip.action for track in rig.animation_data.nla_tracks
                       for strip in track.strips if strip.action)
    if any(not _local(action) or action.users > 1 for action in actions):
        raise ValueError('Dress animation uses a linked/shared Action; names were retained.')


def _state(rig):
    """Index-based checks survive names; coordinates/weights never get written."""
    bones = tuple((bone.as_pointer(), tuple(bone.head_local), tuple(bone.tail_local),
                   tuple(v for row in bone.matrix_local for v in row),
                   bone.parent.as_pointer() if bone.parent else 0)
                  for bone in rig.data.bones)
    pose = tuple((pb.as_pointer(), tuple(pb.location), pb.rotation_mode,
                  tuple(pb.rotation_euler), tuple(pb.rotation_quaternion),
                  tuple(pb.rotation_axis_angle), tuple(pb.scale),
                  tuple(v for row in pb.matrix for v in row),
                  getattr(pb, 'select', getattr(pb.bone, 'select', False)))
                 for pb in rig.pose.bones)
    groups = tuple((obj.as_pointer(), tuple((g.as_pointer(), g.index, g.lock_weight)
                                           for g in obj.vertex_groups))
                   for obj in bpy.data.objects if _bound(obj, rig))
    return bones, pose, groups


def _validate(source):
    record = skirt.read_record(source)
    skirt._check_existing_geometry(source, record)
    from . import body_original_mode as original, skirt_original_mode as dress
    session = json.loads(source[skirt.RIG_KEY].get(original.SESSION, 'null'))
    if session and session.get('dress_edit'):
        dress.validate_active(bpy.context, source[skirt.RIG_KEY], session['dress_edit'])


def clean(source):
    """Only rename owned legacy selectors; refusal leaves the setup untouched."""
    record = skirt.read_record(source)
    if not record:
        return []
    skirt._check_existing_geometry(source, record)
    rig = source[skirt.RIG_KEY]
    legacy = re.compile(r'^SK_.+_' + re.escape(record['owner'][:6]) + r'(_.+)$')
    prefix = 'SK_' + generated_names.label(source.name, 37)
    expected = set.union(*skirt._bone_collection_layout(record))
    mapping = {name: prefix + match.group(1) for name in expected
               if (match := legacy.fullmatch(name))}
    if not mapping:
        return []
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('Legacy Dress names have ambiguous readable targets.')
    _preflight(source, record, rig, mapping)
    from . import skirt_name_records
    records = skirt_name_records.snapshots(source, rig, mapping)
    bones = [(rig.data.bones[old], old, new) for old, new in sorted(mapping.items())]
    hooks = []
    for name in record['owned_objects']:
        obj = bpy.data.objects.get(name)
        if not obj or obj.get(skirt.OWNER_KEY) != record['owner'] or obj.get(skirt.SOURCE_KEY) != source:
            raise ValueError('Dress helper ownership changed; no names were modified.')
        for modifier in obj.modifiers:
            label = re.fullmatch(re.escape('Control ' + modifier.subtarget) + r'(\.\d{3})?',
                                 modifier.name) if modifier.type == 'HOOK' else None
            if (modifier.type == 'HOOK' and modifier.object == rig
                    and modifier.subtarget in mapping and label):
                desired = 'Control ' + mapping[modifier.subtarget] + (label.group(1) or '')
                if obj.modifiers.get(desired):
                    raise ValueError('A generated Hook name is already in use: ' + desired)
                if obj.animation_data and (obj.animation_data.action or obj.animation_data.drivers
                                            or obj.animation_data.nla_tracks):
                    raise ValueError('An animated Dress wire has legacy Hook labels; names were retained.')
                selector = 'modifiers[' + json.dumps(modifier.name) + ']'
                for collection in (bpy.data.objects, bpy.data.armatures, bpy.data.meshes,
                                   bpy.data.curves, bpy.data.shape_keys, bpy.data.materials,
                                   bpy.data.node_groups, bpy.data.scenes):
                    for holder in collection:
                        animation = getattr(holder, 'animation_data', None)
                        if animation and any(
                            target.id == obj and selector in target.data_path
                            for curve in animation.drivers
                            for variable in curve.driver.variables
                            for target in variable.targets):
                            raise ValueError('A driver references a legacy Dress Hook label; names were retained.')
                hooks.append((modifier, modifier.name, desired))
    bpy.context.view_layer.update()
    before = _state(rig)
    try:
        for bone, old, new in bones:
            bone.name = new  # Blender updates constraints, groups and RNA paths.
            if bone.name != new:
                raise ValueError('Blender could not reserve the planned Dress bone name.')
        for holder, key, raw, updated in records:
            holder[key] = updated
        for modifier, old, new in hooks:
            modifier.name = new
            if modifier.name != new:
                raise ValueError('Blender could not reserve the planned Dress Hook name.')
        bpy.context.view_layer.update()
        _validate(source)
        if _state(rig) != before:
            raise ValueError('Dress pose, rest or group indices changed during name cleanup.')
    except Exception:
        for modifier, old, new in reversed(hooks):
            modifier.name = old
        for bone, old, new in reversed(bones):
            bone.name = old
        for holder, key, raw, updated in records:
            holder[key] = raw
        bpy.context.view_layer.update()
        raise
    return ([{'from': old, 'to': new, 'kind': 'bone'} for bone, old, new in bones]
            + [{'from': old, 'to': new, 'kind': 'hook'} for modifier, old, new in hooks])
