"""Durable display-only checkpoints for an explicitly released limb.

Mesh copies are held by native Armature ID properties, so release/edit/save/
reopen does not depend on Python objects or large vertex arrays in receipts.
Nothing here restores a solver frame, control input or generated owner ID.
"""
import copy
import hashlib
import json
import uuid

import bpy

from . import (body_setup_transaction as transaction, control_colors,
               foot_controls, limb_fk_visuals, limb_ik)


OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'region_styles'
ID_KEY = 'character_designer_region_style_id'
REF_KEY = 'character_designer_region_style_reference'
STATE_KEY = 'character_designer_region_style_payload_sha256'
REFS_KEY = 'character_designer_region_style_meshes_v1'
MARKER_KEY = 'character_designer_region_style_survivors_v1'
VERSION = 1
_POSE_FLAGS = ('rotation_mode', 'lock_location', 'lock_rotation', 'lock_scale',
               'lock_rotation_w', 'lock_rotations_4d',
               'use_transform_at_custom_shape', 'use_transform_around_custom_shape')
_OBJECT_FLAGS = ('location', 'rotation_mode', 'rotation_euler',
                 'rotation_quaternion', 'rotation_axis_angle', 'scale',
                 'hide_render', 'hide_select', 'display_type', 'show_wire',
                 'show_in_front', 'show_name', 'color', 'hide_viewport',
                 'show_bounds', 'display_bounds_type', 'show_axis',
                 'show_all_edges', 'show_transparent', 'show_texture_space')


def _plain(value):
    if isinstance(value, bpy.types.ID):
        raise ValueError('A selected control has an artist ID property reference; preserve that setup before releasing it.')
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, 'items'):
        return {str(k): _plain(v) for k, v in value.items()}
    return [_plain(v) for v in value]


def _artist_properties(owner):
    return {key: _plain(owner[key]) for key in owner.keys()
            if not key.startswith('character_designer_') and key != 'ik_fk'}


def _flags(owner, names):
    return {name: _plain(getattr(owner, name)) for name in names if hasattr(owner, name)}


def _markers(obj):
    try:
        value = json.loads(obj.get(MARKER_KEY, '{}'))
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                               for k, v in value.items()):
            raise ValueError
        return value
    except (ValueError, TypeError) as exc:
        raise ValueError('A selected widget has an invalid regional display checkpoint.') from exc


def _write_markers(obj, values):
    if values:
        obj[MARKER_KEY] = json.dumps(values, sort_keys=True, separators=(',', ':'))
    else:
        obj.pop(MARKER_KEY, None)


def _digest(mesh):
    value = {'geometry': transaction._mesh_geometry(mesh),
             'materials': [material.name_full if material else None for material in mesh.materials]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _identity(obj):
    return {key: _plain(obj[key]) for key in obj.keys()
            if key.startswith('character_designer_') and key != MARKER_KEY}


def _state_digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _descriptor(rig, obj, keys, inventory):
    if obj.get(OWNER_KEY) == limb_ik.OWNER_VALUE:
        return {'owner': limb_ik.OWNER_VALUE, 'kind': obj.get(limb_ik.KIND_KEY)}
    for key in keys:
        foot = inventory['rigs'][key].get('foot_controls')
        if foot:
            for role, entry in foot['widgets'].items():
                if entry['object'] == obj.name:
                    return {'owner': foot_controls.OWNER_VALUE, 'side': key[1], 'role': role}
    rings = limb_fk_visuals.get_record(rig)
    for name, entry in (rings or {}).get('bindings', {}).items():
        if (entry['kind'], entry['side']) in keys and entry['object'] == obj.name:
            return {'owner': limb_fk_visuals.OWNER_VALUE, 'bone': name}
    return {'owner': 'EXTERNAL'}


def capture(context, rig, keys, inventory):
    """Capture selected display state inside the caller's full rig transaction."""
    keys = set(keys)
    token = uuid.uuid4().hex
    names = {bone.name for bone in inventory['bones']
             if (bone.get(limb_ik.KIND_KEY), bone.get(limb_ik.SIDE_KEY)) in keys}
    feet = {key[1]: copy.deepcopy(inventory['rigs'][key]['foot_controls']) for key in keys
            if inventory['rigs'][key].get('foot_controls')}
    for record in feet.values():
        names.update(record['bones'].values())
    rings = limb_fk_visuals.get_record(rig)
    ring_entries = {n: copy.deepcopy(e) for n, e in (rings or {}).get('bindings', {}).items()
                    if (e['kind'], e['side']) in keys}
    names.update(ring_entries)
    source = limb_ik._load_source_widget_registry(rig)
    source_entries = {n: copy.deepcopy(e) for n, e in (source or {}).get('bones', {}).items()
                      if ('ARM', e['side']) in keys}
    names.update(source_entries)
    sizes = limb_fk_visuals._size_record(rig)
    size_entries = {n: copy.deepcopy(e) for n, e in (sizes or {}).get('bindings', {}).items() if n in names}
    native_display = json.loads(rig.data.get(limb_fk_visuals.NATIVE_DISPLAY_KEY, '{}'))
    state = {'version': VERSION, 'id': token, 'armature_id': inventory['armature_id'],
             'keys': [':'.join(key) for key in sorted(keys)], 'bones': {}, 'widgets': {},
             'foot_visuals': {side: {field: copy.deepcopy(record[field]) for field in
                              ('roll_visual_backup', 'roll_visual_fit') if field in record}
                             for side, record in feet.items()},
             'rings': ring_entries, 'source': source_entries,
             'source_version': (source or {}).get('version', limb_ik.SOURCE_WIDGETS_VERSION),
             'sizes': size_entries,
             'native_display': {n: obj for n, obj in native_display.items() if n in ring_entries}}
    objects = {rig.pose.bones[n].custom_shape for n in names if rig.pose.bones[n].custom_shape}
    objects.update(bpy.data.objects[e['object']] for e in ring_entries.values())
    by_name = {}
    try:
        for index, obj in enumerate(sorted(objects, key=lambda item: item.name)):
            if obj.type != 'MESH' or obj.parent or obj.modifiers or obj.constraints or obj.animation_data or obj.data.animation_data or obj.data.shape_keys:
                raise ValueError('A selected control widget has artist dependencies; preserve that setup before release.')
            key = token + '_' + str(index)
            descriptor = _descriptor(rig, obj, keys, inventory)
            widget = {'descriptor': descriptor, 'object_name': obj.name, 'mesh_name': obj.data.name,
                      'identity': _identity(obj), 'digest': _digest(obj.data),
                      'flags': _flags(obj, _OBJECT_FLAGS),
                      'properties': _artist_properties(obj),
                      'mesh_properties': _artist_properties(obj.data), 'reference': None}
            state['widgets'][str(index)] = widget
            by_name[obj.name] = str(index)
            if descriptor['owner'] != 'EXTERNAL':
                backup = obj.data.copy()
                backup.name = rig.name + ' · Saved ' + ('Leg' if next(iter(keys))[0] == 'LEG' else 'Arm') + ' Display'
                backup[OWNER_KEY], backup[ID_KEY], backup[REF_KEY] = OWNER_VALUE, token, key
                backup[limb_ik.ARMATURE_ID_KEY] = inventory['armature_id']
                widget['reference'] = key
                if REFS_KEY not in rig.data:
                    rig.data[REFS_KEY] = {}
                rig.data[REFS_KEY][key] = backup
                markers = _markers(obj)
                markers[key] = token
                _write_markers(obj, markers)
        for name in sorted(names):
            pb = rig.pose.bones[name]
            display = limb_ik._pose_shape_json_state(pb)
            state['bones'][name] = {'display': display,
                'widget': by_name.get(display['custom_shape']), 'flags': _flags(pb, _POSE_FLAGS),
                'colors': _plain(control_colors.capture_bone(pb)),
                'bone_color': _plain(control_colors._color_state(pb.bone)),
                'hide': bool(pb.bone.hide), 'hide_select': bool(pb.bone.hide_select),
                'show_wire': bool(pb.bone.show_wire) if hasattr(pb.bone, 'show_wire') else None,
                'bone_properties': _artist_properties(pb.bone), 'pose_properties': _artist_properties(pb),
                'metadata': {field: pb.bone[field] for field in
                             (limb_ik.CONTROL_SHAPE_STYLE_KEY, limb_ik.CONTROL_VISUAL_DEFAULT_KEY)
                             if field in pb.bone}}
        digest = _state_digest(state)
        for widget in state['widgets'].values():
            if widget['reference'] is not None:
                rig.data[REFS_KEY][widget['reference']][STATE_KEY] = digest
        validate(rig, state)
        return state
    except Exception:
        cleanup(rig, state)
        raise


def validate(rig, state):
    """Refuse missing or edited backups before rebuilding a released region."""
    if (not isinstance(state, dict) or state.get('version') != VERSION
            or not isinstance(state.get('id'), str) or not state['id']
            or state.get('armature_id') != rig.data.get(limb_ik.ARMATURE_ID_KEY)
            or not isinstance(state.get('widgets'), dict) or not isinstance(state.get('bones'), dict)):
        raise ValueError('The released part has an invalid saved display checkpoint.')
    refs = rig.data.get(REFS_KEY, {})
    digest = _state_digest(state)
    for widget in state['widgets'].values():
        reference = widget.get('reference')
        if reference is None:
            continue
        mesh = refs.get(reference)
        if (not isinstance(mesh, bpy.types.Mesh) or mesh.library
                or mesh.get(OWNER_KEY) != OWNER_VALUE or mesh.get(ID_KEY) != state['id']
                or mesh.get(REF_KEY) != reference or mesh.get(STATE_KEY) != digest
                or mesh.get(limb_ik.ARMATURE_ID_KEY) != state['armature_id']
                or _digest(mesh) != widget['digest']):
            raise ValueError('A released control display backup is missing or changed; preserve the current edit and restore its saved checkpoint.')
    return True


def _new_widget(context, rig, widget, inventory):
    descriptor = widget['descriptor']
    if descriptor['owner'] == limb_ik.OWNER_VALUE:
        tx = limb_ik._new_transaction()
        collection = limb_ik._ensure_widget_collection(context, inventory['armature_id'], tx, armature=rig)
        return limb_ik._ensure_widget(context, inventory['armature_id'], collection,
                                      descriptor['kind'], tx, schema=inventory['schema'])
    if descriptor['owner'] == foot_controls.OWNER_VALUE:
        record = foot_controls.get_record(rig, descriptor['side'])
        return bpy.data.objects[record['widgets'][descriptor['role']]['object']]
    if descriptor['owner'] == limb_fk_visuals.OWNER_VALUE:
        record = limb_fk_visuals.get_record(rig)
        return bpy.data.objects[record['bindings'][descriptor['bone']]['object']]
    obj = bpy.data.objects.get(widget['object_name'])
    if obj is None or obj.type != 'MESH' or _identity(obj) != widget['identity']:
        raise ValueError('An artist control shape disappeared while this part was released.')
    return obj


def _restore_widget(context, rig, state, widget, inventory, allowed_bones):
    reference = widget['reference']
    matches = [obj for obj in bpy.data.objects if reference is not None
               and _markers(obj).get(reference) == state['id']]
    if len(matches) > 1:
        raise ValueError('A released control display has ambiguous surviving widget ownership.')
    if matches:
        obj = matches[0]
        if (obj.type != 'MESH' or _identity(obj) != widget['identity']
                or _digest(obj.data) != widget['digest'] or _flags(obj, _OBJECT_FLAGS) != widget['flags']):
            raise ValueError('A shared control display changed while this part was released; preserve that artist edit before rebuilding.')
        return obj
    obj = _new_widget(context, rig, widget, inventory)
    if widget['descriptor']['owner'] == 'EXTERNAL':
        if _digest(obj.data) != widget['digest'] or _flags(obj, _OBJECT_FLAGS) != widget['flags']:
            raise ValueError('An artist control display changed while this part was released.')
        return obj
    # A missing marker must not authorize rewriting a shared survivor.
    if any(other is not rig or pb.name not in allowed_bones
           for other in bpy.data.objects if other.type == 'ARMATURE'
           for pb in other.pose.bones if pb.custom_shape == obj):
        raise ValueError('A regenerated display is shared with a protected region; no geometry was replaced.')
    backup = rig.data[REFS_KEY][reference]
    # This is a newly generated display resource, not an original mesh. A
    # native Mesh copy preserves required/internal attributes exactly; a
    # from_pydata reconstruction can regenerate them with different values.
    generated = obj.data
    generated_properties = transaction._properties(generated)
    replacement = backup.copy()
    try:
        transaction._set_properties(replacement, generated_properties, {})
        obj.data = replacement
    except Exception:
        if replacement.users == 0:
            bpy.data.meshes.remove(replacement)
        raise
    if generated.users == 0:
        bpy.data.meshes.remove(generated)
    for owner, name in ((obj, widget['object_name']), (obj.data, widget['mesh_name'])):
        collection = bpy.data.objects if owner is obj else bpy.data.meshes
        occupied = collection.get(name)
        if occupied is not None and occupied != owner:
            raise ValueError('A saved control display name is now occupied; keep the artist data intact.')
        owner.name = name
    for name, value in widget['flags'].items():
        setattr(obj, name, value)
    for owner, properties in ((obj, widget['properties']), (obj.data, widget['mesh_properties'])):
        for name, value in properties.items():
            owner[name] = value
    if _digest(obj.data) != widget['digest']:
        raise ValueError('A regenerated control display did not match its saved mesh.')
    return obj


def restore(context, rig, state, inventory, *, allowed_bones=None):
    """Restore only display data, leaving new solver frames and inputs intact."""
    validate(rig, state)
    from . import body_region
    keys = {tuple(value.split(':')) for value in state['keys']}
    if not keys or any(key not in inventory['rigs'] for key in keys):
        raise ValueError('The saved display checkpoint belongs to a different released part.')
    scope = {b.name for b in inventory['bones']
             if (b.get(limb_ik.KIND_KEY), b.get(limb_ik.SIDE_KEY)) in keys}
    for key in keys:
        entry = inventory['rigs'][key]
        scope.update(body_region._native_scope(rig, entry['chain']))
        if entry.get('foot_controls'):
            scope.update(entry['foot_controls']['bones'].values())
    arm_chains = [limb_ik.LimbChain(*key, *inventory['rigs'][key]['chain'])
                  for key in keys if key[0] == 'ARM']
    candidates = limb_ik._source_widget_candidates(rig, arm_chains)
    source_scope = {name for rows in candidates.values() for name, _side in rows}
    if not set(state['source']).issubset(source_scope):
        raise ValueError('A saved source display no longer belongs to this arm.')
    scope.update(source_scope)
    if not set(state['bones']).issubset(scope):
        raise ValueError('A saved control display extends outside its selected body part.')
    # Merge selected source records with the current protected source registry.
    current = limb_ik._load_source_widget_registry(rig)
    source = copy.deepcopy(current) if current else {
        'version': state['source_version'], 'armature_id': state['armature_id'], 'bones': {}}
    selected = set(state['keys'])
    source['bones'] = {n: e for n, e in source['bones'].items() if 'ARM:' + e['side'] not in selected}
    source['bones'].update(copy.deepcopy(state['source']))
    if source['bones']:
        raw = limb_ik._canonical_json(source)
        limb_ik._restore_source_widget_registry_snapshot(context, rig, raw,
                                                        limb_ik._new_transaction(), schema=inventory['schema'])
    allowed_bones = set(state['bones']) if allowed_bones is None else set(allowed_bones)
    widgets = {key: _restore_widget(context, rig, state, value, inventory, allowed_bones)
               for key, value in state['widgets'].items()}
    for name, saved in state['bones'].items():
        pb = rig.pose.bones.get(name)
        if pb is None:
            raise ValueError('A selected control display bone disappeared during local rebuilding.')
        display = saved['display']
        pb.custom_shape = widgets[saved['widget']] if saved['widget'] is not None else None
        transform = display['custom_shape_transform']
        if transform and transform not in rig.pose.bones:
            raise ValueError('A saved display transform is no longer part of this character.')
        pb.custom_shape_transform = rig.pose.bones[transform] if transform else None
        pb.use_custom_shape_bone_size = display['use_bone_size']
        pb.custom_shape_scale_xyz = display['scale']
        pb.custom_shape_translation = display['translation']
        pb.custom_shape_rotation_euler = display['rotation']
        if display['wire_width'] is not None:
            pb.custom_shape_wire_width = display['wire_width']
        mode = saved['flags'].get('rotation_mode', pb.rotation_mode)
        if pb.rotation_mode != mode:
            basis = pb.matrix_basis.copy()
            pb.rotation_mode = mode
            pb.matrix_basis = basis
        for field, value in saved['flags'].items():
            if field != 'rotation_mode':
                setattr(pb, field, value)
        for field in (limb_ik.CONTROL_SHAPE_STYLE_KEY, limb_ik.CONTROL_VISUAL_DEFAULT_KEY):
            if field in saved['metadata']:
                pb.bone[field] = saved['metadata'][field]
            else:
                pb.bone.pop(field, None)
        for owner, properties in ((pb, saved['pose_properties']), (pb.bone, saved['bone_properties'])):
            for field, value in properties.items():
                owner[field] = value
        control_colors.restore_bone_state(pb, saved['colors'])
        control_colors._set_color(pb.bone, saved['bone_color'])
        pb.bone.hide, pb.bone.hide_select = saved['hide'], saved['hide_select']
        if saved['show_wire'] is not None:
            pb.bone.show_wire = saved['show_wire']
    for key, value in state['widgets'].items():
        descriptor, obj = value['descriptor'], widgets[key]
        if descriptor['owner'] == foot_controls.OWNER_VALUE:
            records = foot_controls._all_records(rig)
            records[descriptor['side']]['widgets'][descriptor['role']] = {'object': obj.name, 'mesh': obj.data.name}
            foot_controls._write_records(rig, records)
    records = foot_controls._all_records(rig)
    for side, saved in state['foot_visuals'].items():
        for field in ('roll_visual_backup', 'roll_visual_fit'):
            if field in saved:
                records[side][field] = copy.deepcopy(saved[field])
            else:
                records[side].pop(field, None)
    foot_controls._write_records(rig, records)
    rings = limb_fk_visuals.get_record(rig)
    if state['rings']:
        for name, entry in state['rings'].items():
            rings['bindings'][name]['original'] = copy.deepcopy(entry['original'])
            rings['bindings'][name]['generated'] = copy.deepcopy(entry['generated'])
            descriptor_widget = next(widgets[k] for k, v in state['widgets'].items()
                                     if v['descriptor'] == {'owner': limb_fk_visuals.OWNER_VALUE, 'bone': name})
            rings['bindings'][name]['object'] = descriptor_widget.name
            rings['bindings'][name]['mesh'] = descriptor_widget.data.name
        rig.data[limb_fk_visuals.RECORD_KEY] = json.dumps(rings)
    marker = json.loads(rig.data.get(limb_fk_visuals.NATIVE_DISPLAY_KEY, '{}'))
    for name in state['rings']:
        marker.pop(name, None)
    marker.update(state['native_display'])
    if marker:
        rig.data[limb_fk_visuals.NATIVE_DISPLAY_KEY] = json.dumps(marker)
    else:
        rig.data.pop(limb_fk_visuals.NATIVE_DISPLAY_KEY, None)
    sizes = limb_fk_visuals._size_record(rig) or {
        'version': limb_fk_visuals.VERSION, 'armature_id': state['armature_id'], 'bindings': {}}
    sizes['bindings'].update(copy.deepcopy(state['sizes']))
    if sizes['bindings']:
        rig.data[limb_fk_visuals.IK_SIZE_RECORD_KEY] = json.dumps(sizes)
    for key in state['keys']:
        kind, side = key.split(':')
        data = inventory['rigs'][(kind, side)]
        pole = rig.data.bones[data['pole'].name]
        limb_ik._sync_pole_connector_visibility(inventory, pole, limb_ik._control_shape_style(pole))


def cleanup(rig, state):
    """Consume this token only after commit, or clean its unreferenced failure debris."""
    if not state:
        return
    token = state.get('id')
    references = {v.get('reference') for v in state.get('widgets', {}).values()} - {None}
    refs = rig.data.get(REFS_KEY, {})
    for reference in references:
        mesh = refs.get(reference)
        if (isinstance(mesh, bpy.types.Mesh) and mesh.get(OWNER_KEY) == OWNER_VALUE
                and mesh.get(ID_KEY) == token and mesh.get(REF_KEY) == reference):
            del refs[reference]
    if REFS_KEY in rig.data and not len(rig.data[REFS_KEY]):
        del rig.data[REFS_KEY]
    for obj in bpy.data.objects:
        if MARKER_KEY not in obj:
            continue
        markers = _markers(obj)
        remaining = {k: v for k, v in markers.items() if k not in references or v != token}
        if remaining != markers:
            _write_markers(obj, remaining)
    for mesh in tuple(bpy.data.meshes):
        if (mesh.get(OWNER_KEY) == OWNER_VALUE and mesh.get(ID_KEY) == token
                and mesh.get(REF_KEY) in references and mesh.users == 0):
            bpy.data.meshes.remove(mesh)


def retain_for_recovery(rig, state):
    """Keep native backup users if the caller reports an incomplete rollback."""
    if not state:
        return
    references = {v.get('reference') for v in state.get('widgets', {}).values()} - {None}
    for mesh in bpy.data.meshes:
        reference = mesh.get(REF_KEY)
        if (mesh.get(OWNER_KEY) != OWNER_VALUE or mesh.get(ID_KEY) != state.get('id')
                or reference not in references):
            continue
        if REFS_KEY not in rig.data:
            rig.data[REFS_KEY] = {}
        refs = rig.data[REFS_KEY]
        if refs.get(reference) in (None, mesh):
            refs[reference] = mesh


def backup_tokens(rig):
    """Identify this armature's backups without retaining numeric ID addresses."""
    return {mesh.get(ID_KEY) for mesh in bpy.data.meshes
            if mesh.get(OWNER_KEY) == OWNER_VALUE
            and mesh.get(limb_ik.ARMATURE_ID_KEY) == rig.data.get(limb_ik.ARMATURE_ID_KEY)}


def created_backups(rig, previous_tokens):
    """Find synchronous failure debris even if a release wrapper raised late."""
    states = {}
    for mesh in bpy.data.meshes:
        token = mesh.get(ID_KEY)
        reference = mesh.get(REF_KEY)
        if (mesh.get(OWNER_KEY) != OWNER_VALUE or token in previous_tokens
                or mesh.get(limb_ik.ARMATURE_ID_KEY) != rig.data.get(limb_ik.ARMATURE_ID_KEY)
                or not isinstance(token, str) or not isinstance(reference, str)):
            continue
        state = states.setdefault(token, {'id': token, 'widgets': {}})
        state['widgets'][reference] = {'reference': reference}
    return list(states.values())
