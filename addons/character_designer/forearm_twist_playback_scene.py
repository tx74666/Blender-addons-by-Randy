"""Disposable scene graph for private frozen-playback validation.

This first implementation is intentionally restricted to a background worker.
It does not register operators, handlers or timers and cannot replace an artist
scene. Authoring preflight/templates must be captured before copying the graph.
The source IDs are retained; writable animation/data/constraint references in
the copy are remapped. Unmapped external Object dependencies fail preparation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import bpy


class PlaybackSceneError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise PlaybackSceneError(message)


def _pointer(value):
    return int(value.as_pointer())


_DATA_COLLECTIONS = ('objects', 'scenes', 'collections', 'meshes', 'armatures',
                     'curves', 'cameras', 'lights', 'lattices', 'metaballs',
                     'materials', 'worlds', 'node_groups', 'actions', 'shape_keys')
_GEOMETRY_COLLECTIONS = frozenset((
    'vertices', 'edges', 'loops', 'polygons', 'loop_triangles', 'attributes',
    'uv_layers', 'color_attributes', 'vertex_colors', 'skin_vertices',
    'vertex_paint_masks', 'splines', 'points', 'bezier_points',
    'users_collection', 'users_scene', 'children', 'children_recursive',
    'parent_recursive', 'all_objects', 'objects',
))
_READONLY_OBJECT_POINTERS = frozenset(('custom_shape',))
_SUPPORTED_OBJECT_TYPES = frozenset(('EMPTY', 'MESH', 'ARMATURE', 'CURVE', 'SURFACE',
                                    'FONT', 'META', 'CAMERA', 'LIGHT', 'LATTICE'))


def _inventory():
    return {name: {_pointer(item) for item in getattr(bpy.data, name)}
            for name in _DATA_COLLECTIONS if hasattr(bpy.data, name)}


def _copy_id_properties(source, target, id_map):
    """Remap native ID-valued custom properties, never serialized text."""
    owned_pointers = {_pointer(value) for value in id_map.values()}
    def walk(group):
        try:
            keys = tuple(group.keys())
        except TypeError as error:
            if str(error) != "bpy_struct.keys(): this type doesn't support IDProperties":
                raise
            return
        for key in keys:
            value = group[key]
            if isinstance(value, bpy.types.ID):
                if value in id_map:
                    group[key] = id_map[value]
                elif _pointer(value) in owned_pointers:
                    continue
                elif isinstance(value, bpy.types.Object):
                    raise PlaybackSceneError('Unmapped external Object custom property: ' + str(key))
            elif hasattr(value, 'keys') and hasattr(value, '__setitem__'):
                walk(value)
    walk(target)


def _remap_rna(struct, id_map, visited, external):
    """Only traverse writable copied RNA, not geometry or another ID."""
    owner = getattr(struct, 'id_data', None)
    owned_pointers = {_pointer(value) for value in id_map.values()}
    _require(owner is not None and _pointer(owner) in owned_pointers,
             'RNA substructure is not owned by a copied ID: ' + struct.bl_rna.identifier)
    pointer = _pointer(struct)
    if pointer in visited:
        return
    visited.add(pointer)
    if hasattr(struct, 'keys') and hasattr(struct, '__setitem__'):
        _copy_id_properties(None, struct, id_map)
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if name in {'rna_type', 'id_data', 'original', 'evaluated_get', 'depsgraph'}:
            continue
        if prop.type not in {'POINTER', 'COLLECTION'}:
            continue
        try:
            value = getattr(struct, name)
        except (AttributeError, RuntimeError, ReferenceError):
            continue
        if value is None:
            continue
        if prop.type == 'POINTER':
            if isinstance(value, bpy.types.ID):
                replacement = id_map.get(value)
                if replacement is not None:
                    _require(not prop.is_readonly,
                             f'Cannot remap readonly dependency {struct.bl_rna.identifier}.{name}')
                    setattr(struct, name, replacement)
                elif _pointer(value) in owned_pointers:
                    continue
                elif isinstance(value, bpy.types.Object) and name not in _READONLY_OBJECT_POINTERS:
                    external.append((struct.bl_rna.identifier, name, value.name))
            elif hasattr(value, 'bl_rna') and hasattr(value, 'as_pointer'):
                _remap_rna(value, id_map, visited, external)
        elif name not in _GEOMETRY_COLLECTIONS:
            for item in value:
                # ID collections are linked explicitly by the builder.
                if not isinstance(item, bpy.types.ID) and hasattr(item, 'bl_rna'):
                    _remap_rna(item, id_map, visited, external)


def _actions_for_id(item):
    animation = getattr(item, 'animation_data', None)
    if animation is None:
        return ()
    actions = []
    if animation.action is not None:
        actions.append(animation.action)
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.action is not None:
                actions.append(strip.action)
    return tuple(actions)


def _action_curves(action):
    """Read both layered and legacy Action channels without selecting slots."""
    for layer in getattr(action, 'layers', ()):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                yield from bag.fcurves
    yield from getattr(action, 'fcurves', ())


def _detach_unused_asset_references(session):
    """Clear only orphan authoring-catalog references on the private Scene.

    An asset remembered by the setup UI can survive unlinking its object from
    every Scene. It is not part of this playback graph. Retain the catalog row,
    role and ordering, but do not retain a writable source Object alias there.
    Any animated catalog dependency or reference to another Scene is rejected.
    Generic Object pointers still require an explicitly copied dependency.
    """
    source, target = session.source_scene, session.scene
    prefix = 'character_designer_setup'
    # Reading an unset PointerProperty can materialize authoring storage.
    # A missing catalog has no orphan references to detach on either Scene.
    if source.bl_rna.properties.get(prefix) is None or not source.is_property_set(prefix):
        return
    _require(target.bl_rna.properties.get(prefix) is not None and target.is_property_set(prefix),
             'Copied Scene lost its stored Character Designer asset catalog')
    old_setup = getattr(source, prefix, None)
    if old_setup is None:
        return
    new_setup = getattr(target, prefix, None)
    _require(new_setup is not None and len(old_setup.assets) == len(new_setup.assets),
             'Copied Scene lost its Character Designer asset catalog')
    stale = []
    members = set(source.objects)
    for index, (old, new) in enumerate(zip(old_setup.assets, new_setup.assets)):
        obj = old.object
        _require(new.object == obj and new.role == old.role,
                 'Copied asset catalog differs before reference isolation')
        if obj is None or obj in members:
            continue
        _require(isinstance(obj, bpy.types.Object) and not obj.users_scene,
                 'External asset catalog Object belongs to another Scene')
        _require(old.bl_rna.identifier == new.bl_rna.identifier == 'CharacterDesignerAsset'
                 and _pointer(old.id_data) == _pointer(source)
                 and _pointer(new.id_data) == _pointer(target)
                 and _pointer(old) != _pointer(new),
                 'Asset catalog row is not an independently copied Scene property')
        stale.append((index, old, new, obj, old.role))
    if not stale:
        return
    # Clearing UI references must not change inputs consumed by animation.
    source_animation = getattr(source, 'animation_data', None)
    _require(not any(getattr(strip, 'strips', ())
                     for track in getattr(source_animation, 'nla_tracks', ())
                     for strip in track.strips),
             'Nested Scene NLA requires explicit asset catalog isolation')
    for action in _actions_for_id(source):
        _require(not any(prefix in curve.data_path for curve in _action_curves(action)),
                 'Animated asset catalog cannot be detached for playback')
    for item in session.id_map:
        animation = getattr(item, 'animation_data', None)
        for curve in getattr(animation, 'drivers', ()):
            _require(not (item == source and prefix in curve.data_path),
                     'Driven asset catalog cannot be detached for playback')
            driver = curve.driver
            _require(getattr(driver, 'type', None) != 'SCRIPTED'
                     or bool(getattr(driver, 'is_simple_expression', False)),
                     'Non-simple scripted driver requires explicit asset catalog isolation')
            _require(prefix not in getattr(driver, 'expression', ''),
                     'Driver expression reads the authoring asset catalog')
            for variable in driver.variables:
                for reference in variable.targets:
                    _require(not (reference.id == source and prefix in reference.data_path),
                             'Driver target reads the authoring asset catalog')
    for index, old, new, obj, role in stale:
        new.object = None
        _require(new.object is None and old.object == obj and old.role == role,
                 'Private asset detachment changed the source catalog')
        session.detached_asset_references.append({
            'path': f'character_designer_setup.assets[{index}].object',
            'source_object': obj.name,
            'source_session_uid': getattr(obj, 'session_uid', None),
            'reason': 'Object is unlinked from every Scene; authoring catalog only',
        })


def _remap_animation(source, target, id_map):
    old, new = getattr(source, 'animation_data', None), getattr(target, 'animation_data', None)
    if old is None:
        return
    _require(new is not None, 'Copied ID lost animation data: ' + source.name)
    _require(_pointer(old) != _pointer(new), 'Copied ID shares author AnimationData: ' + source.name)
    old_drivers = tuple(old.drivers)
    new_drivers = tuple(new.drivers)
    _require(len(old_drivers) == len(new_drivers), 'Copied ID lost driver curves: ' + source.name)
    _require(all(_pointer(a) != _pointer(b) for a, b in zip(old_drivers, new_drivers)),
             'Copied ID shares author driver curves: ' + source.name)
    _require(len(old.nla_tracks) == len(new.nla_tracks), 'Copied ID changed NLA track count: ' + source.name)
    _require(all(_pointer(a) != _pointer(b) for a, b in zip(old.nla_tracks, new.nla_tracks)),
             'Copied ID shares author NLA tracks: ' + source.name)
    if old.action is not None:
        handle = getattr(old.action_slot, 'handle', None) if hasattr(old, 'action_slot') else None
        new.action = id_map[old.action]
        if handle is not None:
            slot = next((slot for slot in new.action.slots if slot.handle == handle), None)
            _require(slot is not None, 'Copied Action lost the current slot: ' + source.name)
            new.action_slot = slot
        elif hasattr(old, 'action_slot'):
            new.action_slot = None
    for old_track, new_track in zip(old.nla_tracks, new.nla_tracks):
        _require(len(old_track.strips) == len(new_track.strips), 'Copied NLA strip count changed')
        for old_strip, new_strip in zip(old_track.strips, new_track.strips):
            _require(_pointer(old_strip) != _pointer(new_strip), 'Copied ID shares an author NLA strip')
            if old_strip.action is not None:
                handle = getattr(old_strip, 'action_slot_handle', None)
                old_slot = getattr(old_strip, 'action_slot', None)
                new_strip.action = id_map[old_strip.action]
                if hasattr(old_strip, 'action_slot') and old_slot is None:
                    new_strip.action_slot = None
                elif handle is not None and handle != 0 and hasattr(new_strip, 'action_slot'):
                    slot = next((slot for slot in new_strip.action.slots if slot.handle == handle), None)
                    _require(slot is not None, 'Copied NLA Action lost its slot')
                    new_strip.action_slot = slot


@dataclass
class PlaybackSceneSession:
    source_scene: object
    scene: object
    object_map: dict = field(default_factory=dict)
    id_map: dict = field(default_factory=dict)
    objects: dict = field(default_factory=dict)
    created: list = field(default_factory=list)
    closed: bool = False
    bindings: list = field(default_factory=list)
    detached_asset_references: list = field(default_factory=list)

    def inspect(self):
        return {
            'schema': 'character-designer.private-playback-scene/1',
            'closed': self.closed,
            'source_scene': self.source_scene.name,
            'scene': self.scene.name if self.scene is not None else None,
            'copied_objects': len(self.object_map),
            'copied_ids': len(self.id_map),
            'calibrated_meshes': [source.name for source in self.objects],
            'background_only': True,
            'registered_handlers': False,
            'detached_asset_references': list(self.detached_asset_references),
        }

    def close(self):
        if self.closed:
            return
        for binding in reversed(self.bindings):
            binding.close()
        self.bindings.clear()
        for window in bpy.context.window_manager.windows:
            if window.scene == self.scene:
                window.scene = self.source_scene
        errors = []
        # Remove owned graph roots before their data. The original IDs are
        # never included, even if someone creates other IDs after preparation.
        order = ('scenes', 'objects', 'collections', 'meshes', 'armatures',
                 'curves', 'cameras', 'lights', 'lattices', 'metaballs',
                 'materials', 'worlds', 'node_groups', 'actions', 'shape_keys')
        for collection_name in order:
            collection = getattr(bpy.data, collection_name, None)
            if collection is None:
                continue
            current = {_pointer(item): item for item in collection}
            for owned_collection, pointer, session_uid in reversed(self.created):
                if owned_collection != collection_name or pointer not in current:
                    continue
                item = current[pointer]
                try:
                    _require(getattr(item, 'session_uid', None) == session_uid,
                             'Owned cleanup pointer was reused; retain the foreign replacement')
                    if hasattr(item, 'use_fake_user'):
                        item.use_fake_user = False
                    collection.remove(item, do_unlink=True)
                except (PlaybackSceneError, ReferenceError, RuntimeError, TypeError) as exc:
                    errors.append(f'{collection_name}/{pointer}: {type(exc).__name__}: {exc}')
        _require(not errors, 'Private scene cleanup failed: ' + '; '.join(errors))
        self.closed = True
        self.scene = None
        self.object_map.clear()
        self.id_map.clear()
        self.objects.clear()


def build_isolated_scene(source_scene):
    """Copy a complete private source graph; no frame change or source edit.

    Capture authoritative templates/golden output before calling. Serial
    callers may add bindings to session.bindings for ownership-aware cleanup.
    """
    _require(bpy.app.background, 'This validation builder is not an artist scene operator.')
    _require(source_scene is not None and source_scene.library is None,
             'A local private source scene is required.')
    for obj in source_scene.objects:
        _require(obj.type in _SUPPORTED_OBJECT_TYPES,
                 'Unsupported writable playback data type: ' + obj.type)
        _require(not getattr(obj, 'particle_systems', ()),
                 'Particle-system isolation is not implemented: ' + obj.name)
    from . import forearm_twist as authoring
    before = _inventory()
    scene = source_scene.copy()
    scene.name = source_scene.name + ' Playback'
    session = PlaybackSceneSession(source_scene, scene)
    session.id_map[source_scene] = scene

    def track_owned(item):
        pointer = _pointer(item)
        session_uid = getattr(item, 'session_uid', None)
        _require(isinstance(session_uid, int) and session_uid > 0,
                 'Native session UID is required for cleanup ownership: ' + item.name)
        for collection_name in _DATA_COLLECTIONS:
            collection = getattr(bpy.data, collection_name, ())
            if any(_pointer(candidate) == pointer for candidate in collection):
                token = (collection_name, pointer, session_uid)
                if token not in session.created:
                    session.created.append(token)
                return
        # Embedded node trees/master collections are removed with their ID.
        _require(bool(getattr(item, 'is_embedded_data', False)),
                 'Copied ID has no supported cleanup collection: ' + item.name)
    track_owned(scene)

    def copy_id(item):
        if item in session.id_map:
            return session.id_map[item]
        _require(bool(getattr(item, 'is_embedded_data', False))
                 or any(any(_pointer(candidate) == _pointer(item) for candidate in getattr(bpy.data, name, ()))
                        for name in _DATA_COLLECTIONS),
                 'Unsupported writable data type before copying: ' + item.bl_rna.identifier)
        result = item.copy()
        _require(_pointer(result) != _pointer(item), 'ID.copy did not create private data')
        track_owned(result)
        result.name = item.name + ' Playback'
        session.id_map[item] = result
        # Mesh/Material/World/Scene .copy creates an owned embedded Key/tree.
        for attribute in ('shape_keys', 'node_tree'):
            old, new = getattr(item, attribute, None), getattr(result, attribute, None)
            if old is not None:
                _require(new is not None and _pointer(old) != _pointer(new),
                         'Copy shares embedded writable data: ' + item.name + '/' + attribute)
                track_owned(new)
                session.id_map[old] = new
        return result

    try:
        source_compositor = getattr(source_scene, 'node_tree', None)
        if source_compositor is not None:
            copied_compositor = getattr(scene, 'node_tree', None)
            _require(copied_compositor is not None and _pointer(copied_compositor) != _pointer(source_compositor),
                     'Scene copy shares its author compositor tree')
            session.id_map[source_compositor] = copied_compositor
            track_owned(copied_compositor)
        # Blender 5.2 uses a regular NodeTree ID for the scene compositor.
        # Scene.copy keeps that reference, so it must be explicitly owned.
        source_compositor_group = getattr(source_scene, 'compositing_node_group', None)
        if source_compositor_group is not None:
            scene.compositing_node_group = copy_id(source_compositor_group)
        _require(_pointer(scene.collection) != _pointer(source_scene.collection),
                 'Scene copy shares the source master collection')
        session.id_map[source_scene.collection] = scene.collection
        track_owned(scene.collection)
        for obj in tuple(scene.collection.objects):
            scene.collection.objects.unlink(obj)
        for child in tuple(scene.collection.children):
            scene.collection.children.unlink(child)

        def copy_collection(source, target):
            target.hide_viewport, target.hide_render = source.hide_viewport, source.hide_render
            for obj in source.objects:
                if obj not in session.object_map:
                    clone = copy_id(obj)
                    session.object_map[obj] = clone
                    if obj.data is not None:
                        if isinstance(obj.data, bpy.types.Image):
                            _require(obj.type == 'EMPTY', 'Shared Image data is supported only on reference empties')
                            # Reference-image pixels remain a shared read-only
                            # asset, like images in copied shader node trees.
                            clone.data = obj.data
                        else:
                            clone.data = copy_id(obj.data)
                        # No authoring handler is allowed to resolve cloned
                        # Original20 ownership. Validated templates own it.
                        if obj.type == 'MESH' and authoring.RECORD_KEY in obj:
                            session.objects[obj] = clone
                            if authoring.RECORD_KEY in clone:
                                del clone[authoring.RECORD_KEY]
                target.objects.link(session.object_map[obj])
            for child in source.children:
                if child not in session.id_map:
                    session.id_map[child] = bpy.data.collections.new(child.name + ' Playback')
                    track_owned(session.id_map[child])
                    copy_collection(child, session.id_map[child])
                target.children.link(session.id_map[child])
        copy_collection(source_scene.collection, scene.collection)

        # Own materials/worlds/node groups with time-dependent node/driver
        # dependencies. Textures and images remain read-only shared assets.
        if source_scene.world is not None:
            scene.world = copy_id(source_scene.world)
        materials = {material for obj in source_scene.objects if obj.data is not None
                     for material in getattr(obj.data, 'materials', ()) if material is not None}
        materials.update(slot.material for obj in source_scene.objects
                         for slot in obj.material_slots if slot.material is not None)
        for material in materials:
            copy_id(material)
        geometry_trees = [modifier.node_group for obj in source_scene.objects
                          for modifier in obj.modifiers if modifier.type == 'NODES' and modifier.node_group is not None]
        for tree in geometry_trees:
            copy_id(tree)
        pending_trees = [getattr(item, 'node_tree', None) for item in tuple(session.id_map.values())]
        pending_trees += [item for item in tuple(session.id_map.values()) if isinstance(item, bpy.types.NodeTree)]
        pending_trees += geometry_trees
        seen_trees = set()
        while pending_trees:
            tree = pending_trees.pop()
            if tree is None or _pointer(tree) in seen_trees:
                continue
            seen_trees.add(_pointer(tree))
            for node in tree.nodes:
                child = getattr(node, 'node_tree', None)
                if child is not None:
                    copy_id(child)
                    pending_trees.append(child)

        # Snapshot the map before copying Actions so no shared author Action
        # is remapped in place. Copy each once to retain shared slot semantics.
        for source in tuple(session.id_map):
            for action in _actions_for_id(source):
                copy_id(action)
        _detach_unused_asset_references(session)
        for source, target in tuple(session.id_map.items()):
            _remap_animation(source, target, session.id_map)
            _copy_id_properties(source, target, session.id_map)
        external, visited = [], set()
        for target in tuple(session.id_map.values()):
            _remap_rna(target, session.id_map, visited, external)
        _require(not external, 'External Object dependencies need explicit isolation: ' + repr(external[:12]))
        if source_scene.camera is not None:
            scene.camera = session.object_map[source_scene.camera]

        # Collection visibility is a layer property, distinct from ID flags.
        def layer_map(layer):
            result = {}
            def visit(item):
                result[item.collection] = item
                for child in item.children:
                    visit(child)
            visit(layer.layer_collection)
            return result
        for source_layer, target_layer in zip(source_scene.view_layers, scene.view_layers):
            original_layers, copied_layers = layer_map(source_layer), layer_map(target_layer)
            for collection, original_layer in original_layers.items():
                copied = copied_layers[session.id_map[collection]]
                for attribute in ('exclude', 'hide_viewport', 'holdout', 'indirect_only'):
                    setattr(copied, attribute, getattr(original_layer, attribute))
            target_layer.update()
        _require(len(scene.objects) == len(source_scene.objects), 'Copied scene object membership changed')
        _require(set(session.object_map) == set(source_scene.objects), 'Source scene object mapping is incomplete')
        after = _inventory()
        observed = {(name, pointer) for name, values in after.items()
                    for pointer in values - before.get(name, set())}
        _require(observed == {(name, pointer) for name, pointer, uid in session.created},
                 'Unowned native IDs appeared during copy/update; do not delete foreign creations')
        return session
    except BaseException:
        # Direct copies/new IDs and their known implicit children are ours.
        # Unexpected callback-created IDs are retained for diagnostic evidence.
        session.close()
        raise
