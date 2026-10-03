"""Readable generated ID names; ownership stays in private properties.

Legacy cleanup renames owned objects/data/collections and their recovery records.
Bone names and animation paths are deliberately preserved in existing rigs.
"""
import json
import re


def _shorten(text, budget):
    return text.encode('utf-8')[:budget].decode('utf-8', errors='ignore').rstrip('_.-')


def label(value, budget=40):
    text = re.sub(r'[^\w.-]+', '_', str(value), flags=re.UNICODE).strip('_.-')
    return _shorten(text or 'Character', budget) or 'Character'


def _role(value):
    if match := re.fullmatch(r'Torso_FK_(\d+)', value):
        value = f'Torso_FK_{int(match.group(1)) + 1:02d}'
    parts = label(value, 200).split('_')
    return '_'.join(part if part in {'IK', 'FK', 'CD'} or not part.isupper()
                    else part.title() for part in parts)


def widget_name(armature, role):
    role = _role(role)
    # Leave room for Blender's numbered collision suffix and preserve side labels.
    if len(role.encode('utf-8')) > 35:
        side = role[-2:] if role.endswith(('.L', '.R')) else ''
        role = _shorten(role[:-2] if side else role, 35 - len(side)) + side
    rig = label(armature.name, 59 - len(('WGT__' + role).encode('utf-8')))
    return 'WGT_' + rig + '_' + role


def collection_name(armature, role):
    return _shorten(armature.name + ' · ' + role, 59)


def skirt_prefix(source):
    base = 'SK_' + label(source.name, 37)
    candidate, index = base, 1
    # Artist groups must never receive weights intended for generated bones.
    while any(group.name.startswith(candidate + '_') for group in source.vertex_groups):
        index += 1
        candidate = base + f'_{index:02d}'
    return candidate


_OBJECT_FIELDS = {'object', 'custom_shape', 'rig', 'curve', 'owned_objects', 'cage',
                  'proxy', 'colliders', 'parent', 'character', 'source'}
_MESH_FIELDS = {'mesh'}
_COLLECTION_FIELDS = {'collection', 'widget_collection', 'owned_collections'}


def _remap(value, maps, field=None):
    if isinstance(value, dict):
        return {key: _remap(item, maps, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, maps, field) for item in value]
    if isinstance(value, str):
        domain = ('object' if field in _OBJECT_FIELDS else 'mesh' if field in _MESH_FIELDS
                  else 'collection' if field in _COLLECTION_FIELDS else None)
        return maps.get(domain, {}).get(value, value)
    return value


def _records():
    import bpy
    for item in tuple(bpy.data.armatures) + tuple(bpy.data.objects):
        if item.library or not item.is_editable:
            continue
        for key in item.keys():
            if not key.startswith('character_designer'):
                continue
            raw = item.get(key)
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, (dict, list)):
                yield item, key, raw, payload


def _transaction(targets, validate):
    """Read back collision-resolved names and update typed references atomically."""
    before = [(item, item.name) for item, _name, _domain in targets]
    records = list(_records())
    maps = {'object': {}, 'mesh': {}, 'collection': {}}
    changed_records = []
    renamed = []
    try:
        for item, name, domain in targets:
            old = item.name
            item.name = name
            maps[domain][old] = item.name
            renamed.append({'from': old, 'to': item.name})
        for item, key, raw, payload in records:
            updated = _remap(payload, maps)
            if updated != payload:
                changed_records.append((item, key, raw))
                item[key] = json.dumps(updated, ensure_ascii=False)
        validate()
    except Exception:
        for item, old in reversed(before):
            item.name = old
        for item, key, raw in changed_records:
            item[key] = raw
        raise
    return renamed


def _widget_role(module, role, entry, side):
    name = module.__name__.rsplit('.', 1)[-1]
    if name == 'foot_controls':
        return role + '.' + side
    if name == 'root_control':
        return 'Root'
    prefix = {'torso_controls': 'Torso_', 'eye_controls': 'Eyes_',
              'spine_ik_fk': 'Spine_IK_', 'limb_fk_visuals': 'FK_'}.get(name, '')
    return prefix + (entry.get('role', role) if name in {'head_neck_visuals', 'body_detail_visuals'} else role)


def _owned_widget(item, module, record, role):
    return (item.get(module.OWNER_KEY) == module.OWNER_VALUE
            and item.get(module.ID_KEY) == record['id']
            and item.get(module.ROLE_KEY) == role
            and (not hasattr(module, 'SIDE_KEY') or item.get(module.SIDE_KEY) == record['side']))


def _widget_targets(armature):
    import bpy
    from . import widget_collections as manager
    modules = {module.RECORD_KEY: module for module in manager._modules()[1:]}
    resources = manager._resources(armature)
    targets = []
    for resource in resources:
        if not resource['key']:
            continue  # Body IK's existing WGT_Randy names already have no random ID.
        module = modules[resource['key']]
        payload = json.loads(armature.data[resource['key']])
        record = payload['legs'][resource['side']] if resource['side'] else payload
        suffix = re.escape(record['id'][:10]) + r'(?:\.\d{3})?$'
        legacy = re.compile(r'^WGT_CD_.+_' + suffix)
        entries = record.get('widgets', record.get('bindings', {}))
        for role, entry in entries.items():
            obj = bpy.data.objects[entry['object']]
            mesh = bpy.data.meshes[entry['mesh']]
            if not legacy.fullmatch(obj.name) and not legacy.fullmatch(mesh.name):
                continue
            binding_role = entry.get('role', role)
            object_role = binding_role if 'bindings' in record else 'WIDGET'
            mesh_role = binding_role if 'bindings' in record else 'WIDGET_MESH'
            if (not _owned_widget(obj, module, record, object_role)
                    or not _owned_widget(mesh, module, record, mesh_role)
                    or not _owned_widget(resource['collection'], module, record,
                                         'COLLECTION' if 'bindings' in record else 'WIDGET_COLLECTION')):
                raise ValueError('A generated widget has changed ownership, identity or role.')
            if (obj.library or mesh.library or not obj.is_editable or not mesh.is_editable
                    or mesh.users != 1 or obj.data != mesh):
                raise ValueError('A generated widget is linked or its mesh is shared.')
            name = widget_name(armature, _widget_role(module, role, entry, resource['side']))
            if legacy.fullmatch(obj.name):
                targets.append((obj, name, 'object'))
            if legacy.fullmatch(mesh.name):
                targets.append((mesh, name, 'mesh'))
        collection = resource['collection']
        if re.fullmatch(r'^CD_.+_' + suffix, collection.name):
            if not _owned_widget(collection, module, record, 'COLLECTION' if 'bindings' in record else 'WIDGET_COLLECTION'):
                raise ValueError('A generated widget collection has changed ownership, identity or role.')
            if collection.library or not collection.is_editable:
                raise ValueError('A generated widget collection is linked.')
            targets.append((collection, collection_name(armature, resource['label']), 'collection'))
    return targets


def _skirt_targets(source):
    import bpy
    from . import skirt_rig as skirt
    record = skirt.read_record(source)
    skirt._check_existing_geometry(source, record)
    owner = record['owner']
    legacy = re.compile(r'^SK_.+_' + re.escape(owner[:6]) + r'(_.+)$')
    prefix = 'SK_' + label(source.name, 37)
    targets, seen = [], set()
    names = set(record['owned_objects'])
    physics = record.get('physics', {})
    names.update(physics.get('colliders', []))
    if physics.get('proxy'):
        names.add(physics['proxy'])
    for name in names:
        obj = bpy.data.objects.get(name)
        if (obj is None or obj is source or obj.get(skirt.OWNER_KEY) != owner
                or obj.get(skirt.SOURCE_KEY) != source or obj.library or not obj.is_editable):
            raise ValueError('A generated skirt resource is missing, linked, or has changed ownership.')
        for item, domain in ((obj, 'object'), (obj.data, 'mesh')):
            if item is None or item in seen:
                continue
            seen.add(item)
            match = legacy.fullmatch(item.name)
            if match:
                if (item.library or not item.is_editable
                        or domain == 'mesh' and (item.users != 1 or item.get(skirt.OWNER_KEY) != owner)):
                    raise ValueError('A generated skirt data block is linked or shared.')
                targets.append((item, prefix + match.group(1), domain))
    collections = set(record['owned_collections'])
    if physics.get('collection'):
        collections.add(physics['collection'])
    for name in collections:
        collection = bpy.data.collections.get(name)
        if (collection is None or collection.get(skirt.OWNER_KEY) != owner
                or collection.library or not collection.is_editable):
            raise ValueError('A generated skirt collection is missing or has changed ownership.')
        if re.fullmatch(r'Skirt wire and shapes \| ' + re.escape(owner[:6]) + r'(?:\.\d{3})?', name):
            targets.append((collection, 'Skirt Wire and Shapes | ' + label(source.name, 30), 'collection'))
    return targets


def clean_generated_names(context=None):
    """Clean verified legacy ID names, retaining artist names and existing bones."""
    import bpy
    from . import skirt_rig, widget_collections
    context = context or bpy.context
    result = {'renamed': [], 'skipped': []}
    for armature in tuple(bpy.data.objects):
        if (armature.type != 'ARMATURE' or armature.library or armature.data.library
                or not armature.is_editable or armature.data.users != 1 or armature.mode == 'EDIT'
                or not any(key.startswith('character_designer') for key in armature.data.keys())):
            continue
        try:
            targets = _widget_targets(armature)
            if targets:
                result['renamed'].extend(_transaction(targets, lambda: widget_collections._resources(armature)))
        except (ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
            result['skipped'].append({'name': armature.name, 'reason': str(exc)})
    for source in tuple(bpy.data.objects):
        if (skirt_rig.RECORD_KEY not in source or source.library or not source.is_editable
                or source.mode == 'EDIT'):
            continue
        try:
            targets = _skirt_targets(source)
            if targets:
                def validate():
                    record = skirt_rig.read_record(source)
                    skirt_rig._check_existing_geometry(source, record)
                result['renamed'].extend(_transaction(targets, validate))
        except (ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
            result['skipped'].append({'name': source.name, 'reason': str(exc)})
    if result['renamed'] and context.view_layer:
        context.view_layer.update()
    return result


def _cleanup_deferred():
    """Wait until addon_utils registration leaves Blender's restricted data."""
    import bpy
    if getattr(bpy.data, 'objects', None) is None:
        return 0.1
    try:
        result = clean_generated_names()
        if result['skipped']:
            print('Character Designer name cleanup skipped edited resources:', result['skipped'])
    except Exception as exc:
        # Automatic repair must not prevent the add-on from enabling or a
        # valid scene from opening. Per-resource failures remain in the report.
        print('Character Designer name cleanup failed:', exc)
    return None


def _queue_cleanup():
    import bpy
    if not bpy.app.timers.is_registered(_cleanup_deferred):
        bpy.app.timers.register(_cleanup_deferred, first_interval=0.0, persistent=True)


def _after_load(_dummy):
    # Defer until file-load callbacks and data reconstruction have finished.
    _queue_cleanup()


def register_handlers():
    import bpy
    from bpy.app.handlers import persistent
    persistent(_after_load)
    if _after_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_after_load)
    _queue_cleanup()


def unregister_handlers():
    import bpy
    if bpy.app.timers.is_registered(_cleanup_deferred):
        bpy.app.timers.unregister(_cleanup_deferred)
    if _after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_after_load)
