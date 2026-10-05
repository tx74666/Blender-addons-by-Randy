"""Plan exact Dress bone-name updates in managed recovery records.

This module never writes Blender data. The caller applies the returned raw JSON
replacements inside the same transaction as native bone and group renaming.
Ownership IDs, display colors and arbitrary artist properties stay verbatim.
"""
import json


def _read(holder, key):
    raw = holder.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f'The saved {key} record is not JSON text.')
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError(f'The saved {key} record is unreadable.') from None
    if not isinstance(value, dict):
        raise ValueError(f'The saved {key} record is incomplete.')
    return value


def _add(result, holder, key, raw, payload):
    """Serialize changed records without touching the holder or its ID refs."""
    if payload == json.loads(raw):
        return
    if (getattr(holder, 'library', None) or getattr(holder, 'override_library', None)
            or not getattr(holder, 'is_editable', True)):
        raise ValueError('A Dress name recovery record is linked or not editable.')
    updated = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    for index, (item, saved_key, previous, _updated) in enumerate(result):
        if item == holder and saved_key == key:
            if previous != raw:
                raise ValueError('A Dress name recovery record changed during planning.')
            result[index] = (holder, key, raw, updated)
            return
    result.append((holder, key, raw, updated))


def _keys(value, mapping):
    if not isinstance(value, dict):
        raise ValueError('A saved Dress bone dictionary is incomplete.')
    result = {}
    for old, item in value.items():
        new = mapping.get(old, old)
        if new in result:
            raise ValueError('Renaming Dress would merge two saved bone entries.')
        result[new] = item
    return result


def _list(value, mapping, *, sort=False):
    if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
        raise ValueError('A saved Dress bone-name list is incomplete.')
    result = [mapping.get(name, name) for name in value]
    if len(set(value)) == len(value) and len(set(result)) != len(result):
        raise ValueError('Renaming Dress would merge saved bone names.')
    return sorted(result) if sort else result


def _view(value, mapping):
    if not isinstance(value, dict):
        raise ValueError('A saved Dress display snapshot is incomplete.')
    for key in ('hidden', 'pose_hidden'):
        if key in value:
            value[key] = _keys(value[key], mapping)


def _source_record(source, rig, mapping, result):
    from . import skirt_rig as skirt
    saved = _read(source, skirt.RECORD_KEY)
    if (saved is None or source.get(skirt.RIG_KEY) != rig
            or saved.get('owner') != source.get(skirt.OWNER_KEY)):
        raise ValueError('The Dress naming record has lost its rig or ownership.')
    try:
        allowed = set.union(*skirt._bone_collection_layout(saved))
        if set(mapping) - allowed:
            raise ValueError('The Dress name map includes a bone outside its saved ownership.')
        controls = saved['controls']
        for key in ('waist', 'mid', 'hem'):
            controls[key] = mapping.get(controls[key], controls[key])
        for entry in controls['chains']:
            for key in ('mid', 'hem'):
                entry[key] = mapping.get(entry[key], entry[key])
        for chain in saved['chains']:
            for layer in ('manual', 'phys', 'def'):
                chain[layer] = _list(chain[layer], mapping)
        saved['groups'] = _list(saved['groups'], mapping)
        if saved.get('shared'):
            saved['shared']['names'] = _list(saved['shared']['names'], mapping, sort=True)
            if saved.get('character') == rig.name:
                for parent in (saved, saved['shared']):
                    key = 'anchor' if parent is saved['shared'] else 'parent_bone'
                    if key in parent:
                        parent[key] = mapping.get(parent[key], parent[key])
        original = saved.get('original', {})
        if (source.get(skirt.PARENT_KEY) == rig
                and original.get('parent_type') in {'BONE', 'BONE_RELATIVE'}):
            original['parent_bone'] = mapping.get(original['parent_bone'], original['parent_bone'])
    except (KeyError, TypeError, AttributeError):
        raise ValueError('The saved Dress naming record is incomplete.') from None
    _add(result, source, skirt.RECORD_KEY, source[skirt.RECORD_KEY], saved)
    return saved['owner']


def _original_snapshots(objects, source, rig, owner, mapping, result):
    from . import body_original_mode as original, skirt_rig as skirt
    for main in objects:
        raw = main.get(original.SESSION)
        if raw is None:
            continue
        references = main.get(original.DISPLAY_REFS, {})
        keys = {'0'} if main == rig else set()
        if not hasattr(references, 'items'):
            if main == rig:
                raise ValueError('The saved Original display references are incomplete.')
            continue
        keys.update(str(key) for key, target in references.items() if target == rig)
        if not keys:
            continue
        saved = _read(main, original.SESSION)
        if main == rig:
            for field in ('bones', 'names'):
                if field in saved:
                    saved[field] = _list(saved[field], mapping, sort=True)
            for field in ('rest', 'channels', 'entered_channels', 'locks', 'pose'):
                if field in saved:
                    saved[field] = _keys(saved[field], mapping)
            for entry in saved.get('rest', {}).values():
                if entry.get('parent') in mapping:
                    entry['parent'] = mapping[entry['parent']]
            for entry in saved.get('constraints', []):
                if 'bone' in entry:
                    entry['bone'] = mapping.get(entry['bone'], entry['bone'])
            if 'display' in saved:
                _view(saved['display'], mapping)
        for targets in saved.get('native_groups', {}).values():
            for key in keys & set(targets):
                targets[key] = _list(targets[key], mapping, sort=True)
        for key in keys - {'0'}:
            if key in saved.get('extra_bones', {}):
                saved['extra_bones'][key] = _list(saved['extra_bones'][key], mapping, sort=True)
            if key in saved.get('extra_display', {}):
                _view(saved['extra_display'][key], mapping)
        for entry in saved.get('dress_edit', []):
            if entry.get('owner') != owner:
                continue
            if source.get(skirt.RIG_KEY) != rig:
                raise ValueError('The Dress Original recovery target changed during naming.')
            entry['names'] = _list(entry['names'], mapping, sort=True)
            for field in ('rest', 'parents', 'channels', 'locks', 'constraints',
                          'pose', 'entered_channels', 'entered_basis'):
                if field in entry:
                    entry[field] = _keys(entry[field], mapping)
            for key, parent in entry.get('parents', {}).items():
                entry['parents'][key] = mapping.get(parent, parent)
        _add(result, main, original.SESSION, raw, saved)


def _corrections(source, rig, owner, mapping, result):
    from . import skirt_rig as skirt, skirt_original_mode as original
    raw = source.get(original.CORRECTIONS)
    if raw is None:
        return
    saved = _read(source, original.CORRECTIONS)
    if source.get(skirt.RIG_KEY) != rig or saved.get('owner') != owner:
        raise ValueError('The saved Dress correction ownership does not match its naming record.')
    saved['bones'] = _keys(saved['bones'], mapping)
    _add(result, source, original.CORRECTIONS, raw, saved)


def snapshots(source, rig, mapping):
    """Return ``(holder, key, old_json, new_json)`` plans; never mutate IDs.

    ``mapping`` is the caller's collision-checked map for this exact owned Dress
    subset. Native RNA references are handled by the caller's bone transaction.
    """
    import bpy
    from . import control_weight_paint
    if (not isinstance(mapping, dict)
            or any(not isinstance(old, str) or not isinstance(new, str) or not old or not new
                   for old, new in mapping.items())
            or len(set(mapping.values())) != len(mapping)):
        raise ValueError('The Dress bone-name map is incomplete or ambiguous.')
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return []
    objects = tuple(bpy.data.objects)
    for scene in bpy.data.scenes:
        session = scene.get(control_weight_paint.SESSION)
        if session and hasattr(session, 'get') and session.get('rig') == rig:
            raise ValueError('Finish Edit Weights before cleaning Dress bone names.')
    if 'character_designer_skirt_bake_preview' in source:
        raise ValueError('Finish the Dress Bake Preview before cleaning its bone names.')
    result = []
    owner = _source_record(source, rig, mapping, result)
    _original_snapshots(objects, source, rig, owner, mapping, result)
    _corrections(source, rig, owner, mapping, result)
    _aux_snapshots(source, rig, mapping, result)
    return result


def _aux_snapshots(source, rig, mapping, result):
    """Plan typed auxiliary JSON updates; never assign a Blender property.

    Required shared helpers:
      _read(holder, key) -> a newly parsed dict, or None when key is absent.
      _add(result, holder, key, raw, payload) -> append only changed JSON.
    The mapping must already identify validated owned bones on this exact rig.
    Encoded collection backups are edited in place only inside their parsed
    JSON; their native BACKUP_REFS ID properties are never decoded or written.
    """
    import bpy
    from . import bone_collections, bone_display, quick_bind, skirt_rig

    def fail(label):
        raise ValueError('The saved ' + label + ' cannot be safely renamed; recover its saved file first.')

    def group(value, label):
        if (not isinstance(value, dict) or set(value) != {'group'}
                or not isinstance(value['group'], dict)):
            fail(label)
        return value['group']

    def array(value, label):
        if (not isinstance(value, dict) or set(value) != {'array'}
                or not isinstance(value['array'], list)):
            fail(label)
        return value['array']

    def name_keys(value, label):
        if not isinstance(value, dict) or any(not isinstance(name, str) for name in value):
            fail(label)
        updated = {}
        for name, state in value.items():
            renamed = mapping.get(name, name)
            if renamed in updated:
                fail(label + ' name collision')
            updated[renamed] = state
        return updated

    def names(value, label, *, sort=False):
        if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
            fail(label)
        updated = [mapping.get(name, name) for name in value]
        if len(set(updated)) != len(updated):
            fail(label + ' name collision')
        if sort and updated != value:
            updated.sort()
        return updated

    def queue(holder, key, payload, changed):
        if not changed:
            return
        # Refuse a necessary rewrite in protected data, rather than rename the
        # bones and silently leave that recovery state pointing to old names.
        if (getattr(holder, 'library', None) or getattr(holder, 'override_library', None)
                or not getattr(holder, 'is_editable', True)):
            raise ValueError('A linked or protected saved Dress reference prevents this rename.')
        _add(result, holder, key, holder.get(key), payload)

    # Bone Collections: preserve every encoded custom property and ID token.
    payload = _read(rig.data, bone_collections.BACKUP_KEY)
    if payload is not None:
        root = group(payload, 'Bone Collections backup')
        original = group(root.get('original'), 'original Bone Collections layout')
        changed = False
        for field in ('hidden', 'pose_hidden'):
            if field in original:
                wrapped = original[field]
                before = group(wrapped, 'Bone Collections ' + field)
                updated = name_keys(before, 'Bone Collections ' + field)
                if updated != before:
                    wrapped['group'] = updated
                    changed = True
        for wrapped_records, label in (
                (original.get('collections'), 'original Bone Collections'),
                (root.get('managed'), 'managed Bone Collections')):
            for wrapped in array(wrapped_records, label):
                collection = group(wrapped, label + ' record')
                before = array(collection.get('bones'), label + ' bones')
                updated = names(before, label + ' bones', sort=True)
                if updated != before:
                    collection['bones']['array'] = updated
                    changed = True
        queue(rig.data, bone_collections.BACKUP_KEY, payload, changed)

    # Display view payloads are duplicated on all affected Armature data IDs.
    # Scope every sub-snapshot through its own native reference table.
    for data in bpy.data.armatures:
        if bone_display.VIEW_KEY not in data:
            continue
        refs = data.get(bone_display.REFS_KEY, {})
        if not hasattr(refs, 'items'):
            if data == rig.data:
                fail('bone display references')
            continue
        selected = {key for key, target in refs.items() if target == rig}
        if not selected:
            if data == rig.data:
                fail('bone display references')
            continue
        payload = _read(data, bone_display.VIEW_KEY)
        snapshots = payload.get('rigs')
        if not isinstance(snapshots, dict) or not selected.issubset(snapshots):
            fail('bone display view')
        changed = False
        for key in selected:
            state = snapshots[key]
            if not isinstance(state, dict) or 'hidden' not in state:
                fail('bone display view')
            for field in ('hidden', 'pose_hidden'):
                if field in state:
                    before = state[field]
                    updated = name_keys(before, 'bone display ' + field)
                    if updated != before:
                        state[field] = updated
                        changed = True
        queue(data, bone_display.VIEW_KEY, payload, changed)

    # Include detached targets: the saved ID pointers, rather than their live
    # modifier targets or current parents, establish backup ownership.
    for target in bpy.data.objects:
        if target.get(quick_bind.RIG_KEY) == rig and quick_bind.BACKUP_KEY in target:
            payload = _read(target, quick_bind.BACKUP_KEY)
            if payload.get('version') != 1:
                fail('Previous Binding backup')
            before = payload.get('names')
            updated = names(before, 'Previous Binding bone names')
            changed = updated != before
            payload['names'] = updated
            records = payload.get('groups')
            if not isinstance(records, list):
                fail('Previous Binding groups')
            group_names = set()
            for entry in records:
                if not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
                    fail('Previous Binding group')
                old = entry['name']
                renamed = mapping.get(old, old)
                if renamed in group_names:
                    fail('Previous Binding group name collision')
                group_names.add(renamed)
                if renamed != old:
                    entry['name'] = renamed
                    changed = True
            queue(target, quick_bind.BACKUP_KEY, payload, changed)

        if (target.get(quick_bind.REMOVED_RIG_KEY) == rig
                and quick_bind.REMOVED_KEY in target):
            payload = _read(target, quick_bind.REMOVED_KEY)
            if (payload.get('version') != 1
                    or not isinstance(payload.get('parent_removed'), bool)):
                fail('removed binding connection')
            changed = False
            # parent_bone otherwise belongs to a different original parent.
            if payload['parent_removed'] and payload.get('parent_type') in {'BONE', 'BONE_RELATIVE'}:
                old = payload.get('parent_bone')
                if not isinstance(old, str):
                    fail('removed binding parent bone')
                renamed = mapping.get(old, old)
                if renamed != old:
                    payload['parent_bone'] = renamed
                    changed = True
            queue(target, quick_bind.REMOVED_KEY, payload, changed)

        # Separate Dress attachment recovery owns its old parent through this
        # exact native pointer. Never infer it from the JSON parent name.
        if (target.get(skirt_rig.ATTACHMENT_PARENT_KEY) == rig
                and skirt_rig.ATTACHMENT_BACKUP_KEY in target):
            payload = _read(target, skirt_rig.ATTACHMENT_BACKUP_KEY)
            changed = False
            if payload.get('parent_type') == 'BONE':
                old = payload.get('parent_bone')
                if not isinstance(old, str):
                    fail('Dress attachment parent bone')
                renamed = mapping.get(old, old)
                if renamed != old:
                    payload['parent_bone'] = renamed
                    changed = True
            queue(target, skirt_rig.ATTACHMENT_BACKUP_KEY, payload, changed)

    return result
