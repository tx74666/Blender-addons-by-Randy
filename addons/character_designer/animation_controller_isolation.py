"""Own a disposable character and its authenticated bind graph for new motion."""
from contextlib import contextmanager
from dataclasses import dataclass

import bpy


class IsolationError(ValueError):
    """A private character could not retain its authenticated dependencies."""


@dataclass
class PrivateCharacter:
    context: object
    scene: object
    rig: object
    action: object
    diagnostics: dict


def _require(condition, message):
    if not condition:
        raise IsolationError(message)


def _value(value, mapping):
    if isinstance(value, bpy.types.ID):
        return mapping.get(value, value)
    if hasattr(value, 'items'):
        return {key: _value(item, mapping) for key, item in value.items()}
    if hasattr(value, 'to_list'):
        return [_value(item, mapping) for item in value.to_list()]
    if isinstance(value, (tuple, list)):
        return [_value(item, mapping) for item in value]
    return value


def _properties(owner, mapping):
    for key, value in tuple(owner.items()):
        owner[key] = _value(value, mapping)


def _pointers(owner, mapping):
    for prop in owner.bl_rna.properties:
        if prop.type == 'POINTER' and not prop.is_readonly:
            value = getattr(owner, prop.identifier)
            if isinstance(value, bpy.types.ID) and value in mapping:
                setattr(owner, prop.identifier, mapping[value])


def _drivers(owner, mapping):
    animation = owner.animation_data
    if animation is None:
        return
    for curve in animation.drivers:
        for variable in curve.driver.variables:
            for target in variable.targets:
                if target.id in mapping:
                    target.id = mapping[target.id]


def _remap_object(obj, mapping):
    _properties(obj, mapping)
    _pointers(obj, mapping)
    _drivers(obj, mapping)
    for owner in (*obj.constraints, *obj.modifiers):
        _pointers(owner, mapping)
    if obj.type != 'ARMATURE':
        return
    _properties(obj.data, mapping)
    _drivers(obj.data, mapping)
    for bone in obj.data.bones:
        _properties(bone, mapping)
    for collection in obj.data.collections_all:
        _properties(collection, mapping)
    for bone in obj.pose.bones:
        _properties(bone, mapping)
        _pointers(bone, mapping)
        for constraint in bone.constraints:
            _pointers(constraint, mapping)


def _secondary_sources(rig):
    from . import controller_animation, skirt_rig, hair_bones_rig
    names = controller_animation._secondary_bones(rig)
    sources = set(skirt_rig.shared_sources(rig))
    sources.update(bone.get(hair_bones_rig.SOURCE_KEY) for bone in rig.data.bones
                   if bone.get(hair_bones_rig.OWNER_KEY) == hair_bones_rig.OWNER_VALUE)
    return names, sources


def _graph(rig):
    from . import natural_bind_graph as graph
    if graph.KEY not in rig:
        return None
    record = graph.validate(rig)
    for branch in (record, record.get('stable')):
        if not branch:
            continue
        _require(graph._solver_signature(branch['source'], rig) == branch['solver_signature'],
                 'The owned limb solver signature changed.')
        reference = branch.get('neutral_source')
        if reference:
            _require(graph._solver_signature(reference, rig) == branch['neutral_signature'],
                     'The owned neutral limb solver signature changed.')
    _require(graph._rig_references(rig, record) == list(record['rig_references']),
             'The character has additional references to its limb bind helpers.')
    return record


def _cleanup(owned):
    objects, armatures, collections, actions, scene = (
        owned['objects'], owned['armatures'], owned['collections'], owned['actions'], owned['scene'])
    ids = set(objects + armatures + collections + actions)
    if scene:
        ids.update((scene, scene.collection))
    users = bpy.data.user_map(subset=ids)
    if any(owner not in ids for owners in users.values() for owner in owners):
        raise RuntimeError('A private mapping dependency acquired an external user and was retained for recovery.')
    for obj in reversed(objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    if scene:
        bpy.data.scenes.remove(scene)
    for collection in reversed(collections):
        bpy.data.collections.remove(collection)
    for data in reversed(armatures):
        if data.users:
            raise RuntimeError('A private Armature is still in use and was retained for recovery.')
        bpy.data.armatures.remove(data)
    for action in reversed(actions):
        if action.users:
            raise RuntimeError('A private seed Action is still in use and was retained for recovery.')
        bpy.data.actions.remove(action)


@contextmanager
def isolate_target(context, target_rig):
    """Own a mapping target inside a background worker only."""
    with _isolate_target(context, target_rig) as private:
        yield private


@contextmanager
def _background_scene(context, scene):
    """Keep the worker's actual window on its disposable evaluation scene."""
    window = context.window
    if window is None:
        yield
        return
    previous_scene, previous_layer = window.scene, window.view_layer
    try:
        window.scene = scene
        window.view_layer = scene.view_layers[0]
        yield
    finally:
        window.scene = previous_scene
        window.view_layer = previous_layer


@contextmanager
def _isolate_target(context, target_rig):
    """Clone a complete graph, then route an independent new Action normally.

    The background worker selects its private scene and view layer.
    Secondary source mesh objects retain shared read-only mesh data. All
    Armature data and mutable ownership metadata belong to the copies.
    Caller-created result Actions must not reference this scope's owned IDs.
    """
    _require(bpy.app.background, 'Controller mapping requires an owned background worker.')
    from . import limb_ik, controller_animation, bone_collections
    from . import natural_bind_graph as graph, natural_bind_keys as keys
    _require(target_rig is not None and target_rig.type == 'ARMATURE' and target_rig.mode != 'EDIT',
             'Choose a character Armature outside Edit Mode.')
    controller_animation._require_private_evaluation_idle()
    try:
        record = _graph(target_rig)
        secondary_names, secondary_sources = _secondary_sources(target_rig)
    except ValueError as error:
        raise IsolationError(str(error)) from error
    graph_objects = tuple(record['collection'].objects) if record else ()
    _require(all(obj.type in {'ARMATURE', 'EMPTY'} for obj in graph_objects),
             'The limb bind graph contains an unsupported helper type.')
    owned = dict(objects=[], armatures=[], collections=[], actions=[], scene=None)
    try:
        scene = bpy.data.scenes.new('Controller Mapping Preview')
        owned['scene'] = scene
        scene.unit_settings.system = context.scene.unit_settings.system
        scene.unit_settings.scale_length = context.scene.unit_settings.scale_length
        scene.render.fps = context.scene.render.fps
        scene.render.fps_base = context.scene.render.fps_base
        scene.frame_current = context.scene.frame_current
        scene.frame_subframe = context.scene.frame_subframe
        mapping = {context.scene: scene}
        if record:
            collection = bpy.data.collections.new('Controller Mapping Limb Bind')
            owned['collections'].append(collection)
            for key, value in record['collection'].items():
                collection[key] = value
            mapping[record['collection']] = collection
        for original in (target_rig, *graph_objects, *sorted(secondary_sources, key=lambda obj: obj.name_full)):
            clone = original.copy()
            owned['objects'].append(clone)
            mapping[original] = clone
            clone.use_fake_user = False
            if original.type == 'ARMATURE':
                data = original.data.copy()
                owned['armatures'].append(data)
                data.use_fake_user = False
                clone.data = data
                mapping[original.data] = data
            if original is target_rig:
                scene.collection.objects.link(clone)
            elif original in graph_objects:
                collection.objects.link(clone)
        rig = mapping[target_rig]
        for obj in owned['objects']:
            _remap_object(obj, mapping)
        for collection in owned['collections']:
            _properties(collection, mapping)
        # Frame sampling has no presentation updates to publish.
        rig.data[bone_collections.AUTO_KEY] = 0
        if record and record['attached']:
            scene.collection.children.link(mapping[record['collection']])
        action = bpy.data.actions.new('Controller Mapping Input')
        owned['actions'].append(action)
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        animation = rig.animation_data_create()
        animation.action = action
        animation.action_slot = slot
        for track in tuple(animation.nla_tracks):
            animation.nla_tracks.remove(track)
        animation.use_nla = False
        animation.action_blend_type = 'REPLACE'
        animation.action_influence = 1.
        with _background_scene(context, scene), context.temp_override(
                scene=scene, view_layer=scene.view_layers[0], object=rig, active_object=rig):
            private_context = bpy.context
            if record:
                _graph(rig)
                _require(keys.KEY in rig, 'The bind graph has no keyed Action routing record.')
                desired = keys.desired(rig, scene)
                _require(desired is None, 'The new independent Action unexpectedly owns a keyed bind.')
                keys.route(private_context, rig, desired)
                _require(not graph.validate(rig)['attached'], 'The new Action did not detach its private bind outputs.')
            private_context.view_layer.update()
            try:
                inventory = limb_ik._validate_inventory(rig, limb_pose_only=True)
            except ValueError as error:
                raise IsolationError(str(error)) from error
            _require(controller_animation._secondary_bones(rig) == secondary_names,
                     'The private character lost authenticated secondary bone ownership.')
            yield PrivateCharacter(private_context, scene, rig, action,
                dict(bind_graph=record is not None, graph_objects=len(graph_objects),
                     secondary_sources=len(secondary_sources), limbs=len(inventory['rigs']),
                     route='new independent Action through existing keyed bind route'))
    finally:
        _cleanup(owned)
