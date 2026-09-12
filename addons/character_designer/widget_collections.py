"""Keep controller meshes in one owned hierarchy without merging module resources."""
import json
import bpy
from bpy.types import Operator

OWNER_KEY = 'character_designer_owner'
OWNER_VALUE = 'widget_collections'
ROLE_KEY = 'character_designer_widget_container_role'
RIG_KEY = 'character_designer_widget_container_rig'
ROOT_NAME = 'CDesigner Widgets'
_LAYER_FLAGS = ('hide_viewport', 'holdout', 'indirect_only', 'exclude')


def _modules():
    from . import (limb_ik, foot_controls, torso_controls, eye_controls, spine_ik_fk,
                   root_control, limb_fk_visuals, head_neck_visuals, body_detail_visuals)
    return (limb_ik, foot_controls, torso_controls, eye_controls, spine_ik_fk,
            root_control, limb_fk_visuals, head_neck_visuals, body_detail_visuals)


def _parents(collection):
    candidates = list(bpy.data.collections) + [scene.collection for scene in bpy.data.scenes]
    return tuple(parent for parent in candidates if collection.name in parent.children)


def _scenes(collection):
    return tuple(scene for scene in bpy.data.scenes if collection in scene.collection.children_recursive)


def _layer_node(node, collection):
    if node.collection == collection:
        return node
    for child in node.children:
        found = _layer_node(child, collection)
        if found is not None:
            return found
    return None


def _layer_states(collection):
    result = []
    for scene in _scenes(collection):
        for view in scene.view_layers:
            node = _layer_node(view.layer_collection, collection)
            if node is not None:
                result.append((view, {field: getattr(node, field) for field in _LAYER_FLAGS}))
    return result


def _restore_layers(collection, states):
    for view, flags in states:
        node = _layer_node(view.layer_collection, collection)
        if node is None:
            raise ValueError('The widget collection disappeared from its original view layer.')
        for field, value in flags.items():
            setattr(node, field, value)


def _owned(collection, role):
    return (collection is not None and collection.get(OWNER_KEY) == OWNER_VALUE
            and collection.get(ROLE_KEY) == role)


def _editable(context, armature):
    if (armature is None or armature.type != 'ARMATURE' or armature.library or armature.data.library
            or not armature.is_editable or armature.data.users != 1
            or armature.name not in context.scene.objects):
        raise ValueError('Choose a local, single-user character armature in the current scene.')


def _leaf(context, collection):
    if (collection is None or collection.library or not collection.is_editable
            or collection.children or collection.users > 1
            or _scenes(collection) != (context.scene,)):
        raise ValueError('A widget collection is shared, nested with artist data, or outside this scene.')
    modules = _modules()
    owner = next((module for module in modules if collection.get(OWNER_KEY) == module.OWNER_VALUE), None)
    expected = 'COLLECTION' if owner in modules[-3:] else 'WIDGET_COLLECTION'
    if owner is None or collection.get(owner.ROLE_KEY) != expected:
        raise ValueError('Only registered Character Designer widget collections can be organized.')


def _name(collection, name):
    occupied = bpy.data.collections.get(name)
    if occupied is not None and occupied != collection:
        owner = next((m for m in _modules() if collection.get(OWNER_KEY) == m.OWNER_VALUE), None)
        if owner is None or occupied.get(OWNER_KEY) != owner.OWNER_VALUE or occupied.get(owner.ROLE_KEY) != collection.get(owner.ROLE_KEY):
            raise ValueError(f"Collection '{name}' already belongs to another resource.")
        name = _available_name(name, collection)
    collection.name = name


def _available_name(name, current=None):
    index, candidate = 0, name
    while (occupied := bpy.data.collections.get(candidate)) is not None and occupied != current:
        index += 1
        candidate = f'{name}.{index:03d}'
    return candidate


def _reparent(collection, parent):
    if collection == parent or parent in collection.children_recursive:
        raise ValueError('A widget folder cannot contain itself.')
    layer_states = _layer_states(collection)
    if collection.name not in parent.children:
        parent.children.link(collection)
    for old in _parents(collection):
        if old != parent:
            old.children.unlink(collection)
    _restore_layers(collection, layer_states)


def _new(name, role, parent, created, armature=None):
    occupied = bpy.data.collections.get(name)
    if occupied is not None:
        if not _owned(occupied, role):
            raise ValueError(f"Collection '{name}' is occupied; keep or rename that artist collection first.")
        name = _available_name(name)
    collection = bpy.data.collections.new(name)
    created.append(collection)
    collection[OWNER_KEY], collection[ROLE_KEY] = OWNER_VALUE, role
    if armature is not None:
        collection[RIG_KEY] = armature
    parent.children.link(collection)
    return collection


def _containers(context, armature, created):
    roots = [c for c in bpy.data.collections if _owned(c, 'ROOT') and context.scene in _scenes(c)]
    if len(roots) > 1:
        raise ValueError('Multiple CDesigner widget folders exist in this scene; keep their contents intact.')
    root = roots[0] if roots else _new(ROOT_NAME, 'ROOT', context.scene.collection, created)
    if root.objects or root.users != 1 or _parents(root) != (context.scene.collection,):
        raise ValueError('The CDesigner widget folder contains artist objects or external links.')
    rigs = [c for c in root.children if _owned(c, 'RIG') and c.get(RIG_KEY) == armature]
    if len(rigs) > 1:
        raise ValueError('This character has duplicate widget folders.')
    folder = rigs[0] if rigs else _new(armature.name + ' · Widgets', 'RIG', root, created, armature)
    if folder.objects or folder.users != 1 or _parents(folder) != (root,):
        raise ValueError('The character widget folder contains artist objects or external links.')
    return root, folder


def _restore_leaf(collection, name, parents):
    layer_states = _layer_states(collection)
    _name(collection, name)
    for parent in parents:
        if collection.name not in parent.children:
            parent.children.link(collection)
    for parent in _parents(collection):
        if parent not in parents:
            parent.children.unlink(collection)
    _restore_layers(collection, layer_states)


def _discard_created(created):
    pending = list(created)
    while pending:
        removed = []
        for collection in pending:
            if not collection.objects and not collection.children and collection.users <= 1:
                bpy.data.collections.remove(collection)
                removed.append(collection)
        if not removed:
            break
        pending = [collection for collection in pending if collection not in removed]


def ensure_container(context, leaf, armature, label):
    """Group one newly tagged leaf; its caller updates the record with leaf.name."""
    _editable(context, armature)
    _leaf(context, leaf)
    name, parents, layers, created = leaf.name, _parents(leaf), _layer_states(leaf), []
    try:
        _root, folder = _containers(context, armature, created)
        _name(leaf, armature.name + ' · ' + label)
        _reparent(leaf, folder)
    except Exception:
        _restore_leaf(leaf, name, parents)
        _restore_layers(leaf, layers)
        _discard_created(created)
        raise
    return leaf


def prune_empty(context):
    """Remove only unused manager-owned empty folders after module cleanup."""
    removed = []
    for role in ('RIG', 'ROOT'):
        for collection in tuple(bpy.data.collections):
            if (not _owned(collection, role) or collection.library or not collection.is_editable
                    or collection.objects or collection.children or collection.users > 1
                    or _scenes(collection) != (context.scene,)):
                continue
            removed.append(collection.name)
            bpy.data.collections.remove(collection)
    return removed


def _resources(armature):
    modules = _modules()
    limb, foot = modules[:2]
    inventory = limb._validate_inventory(armature)
    result = []
    owned = [c for c in bpy.data.collections if limb._owned(c, inventory['armature_id'], role='WIDGET_COLLECTION')]
    if owned:
        if len(owned) != 1:
            raise ValueError('The body widget collection is duplicated.')
        objects = {obj.name for obj in bpy.data.objects if limb._owned(obj, inventory['armature_id'], role='WIDGET')}
        result.append(dict(collection=owned[0], label='Body IK', objects=objects, key=None, field=None, side=None))
    foot.validate(armature, inventory)
    for side, record in foot.records(armature).items():
        result.append(dict(collection=bpy.data.collections.get(record['widget_collection']), label='Foot.' + side,
                           objects={entry['object'] for entry in record['widgets'].values()},
                           key=foot.RECORD_KEY, field='widget_collection', side=side))
    labels = ('Torso', 'Eyes', 'Spine IK', 'Root', 'FK Rings', 'Head & Neck', 'Breast & Hips')
    for module, label in zip(modules[2:], labels):
        record = module.validate(armature)
        if record is None:
            continue
        field = 'collection' if module in modules[-3:] else 'widget_collection'
        entries = record['bindings'] if module in modules[-3:] else record['widgets']
        result.append(dict(collection=bpy.data.collections.get(record[field]), label=label,
                           objects={entry['object'] for entry in entries.values()},
                           key=module.RECORD_KEY, field=field, side=None))
    return result


def organize(context, armature):
    """Rename/reparent validated leaves and their exact serialized references atomically."""
    _editable(context, armature)
    resources = _resources(armature)
    if not resources:
        return {'collections': 0, 'objects': 0, 'renamed': [], 'deleted': 0, 'skipped': ['No registered widgets.']}
    for item in resources:
        collection = item['collection']
        _leaf(context, collection)
        if set(collection.objects.keys()) != item['objects']:
            raise ValueError(f"Widget collection '{collection.name}' contains unregistered objects.")
    before = [(item['collection'], item['collection'].name, _parents(item['collection'])) for item in resources]
    raw = {item['key']: armature.data[item['key']] for item in resources if item['key']}
    existing = set(bpy.data.collections)
    renamed = []
    try:
        updated = {key: json.loads(value) for key, value in raw.items()}
        for item in resources:
            collection, old = item['collection'], item['collection'].name
            ensure_container(context, collection, armature, item['label'])
            if collection.name != old:
                renamed.append({'from': old, 'to': collection.name})
            if item['key']:
                record = updated[item['key']]
                if item['side'] is not None:
                    record = record['legs'][item['side']]
                record[item['field']] = collection.name
        for key, value in updated.items():
            # No other serialized field or shape/bone reference is rewritten.
            if value != json.loads(raw[key]):
                armature.data[key] = json.dumps(value)
        _resources(armature)
    except Exception:
        for collection, name, parents in reversed(before):
            _restore_leaf(collection, name, parents)
        for key, value in raw.items():
            armature.data[key] = value
        _discard_created([c for c in bpy.data.collections if c not in existing])
        raise
    context.view_layer.update()
    return {'collections': len(resources), 'objects': sum(len(item['objects']) for item in resources),
            'renamed': renamed, 'deleted': 0, 'skipped': []}


class CHARACTERDESIGNER_OT_organize_widgets(Operator):
    bl_idname = 'character_designer.organize_widgets'
    bl_label = 'Organize Controller Widgets'
    bl_description = 'Group registered controller meshes while preserving controls and removal recovery'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .bone_display import character_rig
        try:
            result = organize(context, character_rig(context))
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Organized {result['collections']} widget collections; retained {result['objects']} meshes.")
        return {'FINISHED'}


WIDGET_COLLECTION_CLASSES = (CHARACTERDESIGNER_OT_organize_widgets,)
