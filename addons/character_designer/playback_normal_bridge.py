"""Owned temporary normal bridge for the validated native playback stack.

The add-on registers lifecycle callbacks; installation takes an explicit body.
Source custom normals stay intact. Native Play enables a freshly proven owned
bridge; Stop, save, render and history boundaries disable it safely.
"""

from uuid import uuid4

import bpy
from bpy.app.handlers import persistent


_OWNER_KEY = '_cd_playback_normal_bridge_v1'
_MANAGED = {}
_ACTIVE = {}
_PLAYING = {}
_BLOCKED = set()
_LAST_ERRORS = {}
_RECOVERY_PENDING = False
_REGISTERED = False
_BUSY = False


def _identity(value):
    return value.as_pointer()


def _scene_key(scene):
    return getattr(scene, 'session_uid', None), _identity(scene)


def _error(token, error):
    message = str(error)
    if _LAST_ERRORS.get(token) != message:
        print('Character Designer: playback normal bridge stopped: ' + message)
    _LAST_ERRORS[token] = message


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _source_configuration(obj, modifiers):
    _require(obj.type == 'MESH' and not obj.is_evaluated and obj.mode == 'OBJECT',
             'Bridge requires an original Mesh in Object Mode.')
    mesh = obj.data
    _require(obj.library is None and obj.override_library is None
             and mesh.library is None and mesh.override_library is None
             and mesh.users == 1, 'Bridge requires local, single-user Mesh data.')
    _require(len(obj.users_scene) == 1, 'Bridge requires one owning scene.')
    _require(obj.animation_data is None and mesh.animation_data is None,
             'Object or Mesh AnimationData is unsupported by the bridge.')
    _require(len(modifiers) in (2, 3), 'Bridge requires only Armature and Subsurf.')
    armature, subsurf = modifiers[0], modifiers[-1]
    _require(armature.type == 'ARMATURE' and armature.show_viewport
             and armature.object is not None and armature.object.type == 'ARMATURE',
             'Bridge requires the enabled first Armature.')
    _require(subsurf.type == 'SUBSURF' and subsurf.show_viewport
             and subsurf.subdivision_type == 'CATMULL_CLARK'
             and subsurf.levels == 1 and subsurf.use_limit_surface
             and not subsurf.use_custom_normals,
             'Bridge requires enabled level-1 limit-surface Subsurf without custom normals.')
    attribute = mesh.attributes.get('custom_normal')
    _require(attribute is None or (attribute.domain == 'CORNER'
             and attribute.data_type == 'FLOAT_VECTOR'
             and len(attribute.data) == len(mesh.loops)),
             'Only CORNER/FLOAT_VECTOR custom_normal is supported.')
    return armature, subsurf


def _socket(sockets, identifier):
    matches = [socket for socket in sockets if socket.identifier == identifier]
    _require(len(matches) == 1, 'Bridge Geometry socket identity changed.')
    return matches[0]


def _owned_graph(obj, modifier, token):
    """Prove the actual owned graph for off-only recovery, without enable flags."""
    _require(isinstance(token, str) and len(token) == 32
             and obj.get(_OWNER_KEY) == token,
             'Bridge ownership association changed.')
    modifiers = tuple(obj.modifiers)
    _require(len(modifiers) == 3 and modifiers[1] == modifier
             and modifiers[0].type == 'ARMATURE' and modifiers[-1].type == 'SUBSURF'
             and modifier.type == 'NODES', 'Bridge stack or modifier changed.')
    _require(obj.type == 'MESH' and not obj.is_evaluated
             and obj.library is None and obj.override_library is None,
             'Bridge recovery requires its original local Mesh object.')
    group = modifier.node_group
    _require(group is not None and group.bl_idname == 'GeometryNodeTree'
             and group.library is None and group.override_library is None
             and group.users == 1 and group.get(_OWNER_KEY) == token,
             'Bridge node-group ownership changed.')
    interface = tuple(group.interface.items_tree)
    _require(len(interface) == 2 and all(item.item_type == 'SOCKET'
             and item.socket_type == 'NodeSocketGeometry' for item in interface),
             'Bridge must expose only its two Geometry sockets.')
    inputs = [item for item in interface if item.in_out == 'INPUT']
    outputs = [item for item in interface if item.in_out == 'OUTPUT']
    _require(len(inputs) == len(outputs) == 1, 'Bridge Geometry interface changed.')
    nodes = tuple(group.nodes)
    _require(len(nodes) == 3 and sorted(node.bl_idname for node in nodes) == sorted((
             'NodeGroupInput', 'GeometryNodeRemoveAttribute', 'NodeGroupOutput')),
             'Bridge must have exactly its three owned nodes.')
    _require(all(not node.mute for node in nodes), 'Bridge node is muted.')
    source = next(node for node in nodes if node.bl_idname == 'NodeGroupInput')
    remove = next(node for node in nodes if node.bl_idname == 'GeometryNodeRemoveAttribute')
    output = next(node for node in nodes if node.bl_idname == 'NodeGroupOutput')
    _require(output.is_active_output, 'Bridge output is inactive.')
    name, mode = remove.inputs.get('Name'), remove.inputs.get('Pattern Mode')
    geometry_in, geometry_out = remove.inputs.get('Geometry'), remove.outputs.get('Geometry')
    _require(all(socket is not None for socket in (name, mode, geometry_in, geometry_out))
             and not name.is_linked and not mode.is_linked
             and name.default_value == 'custom_normal' and mode.default_value == 'Exact',
             'Bridge must remove exactly constant custom_normal.')
    source_geometry = _socket(source.outputs, inputs[0].identifier)
    output_geometry = _socket(output.inputs, outputs[0].identifier)
    expected = {(_identity(source_geometry), _identity(geometry_in)),
                (_identity(geometry_out), _identity(output_geometry))}
    links = tuple(group.links)
    _require(len(links) == 2 and all(link.is_valid and not link.is_muted for link in links)
             and {(_identity(link.from_socket), _identity(link.to_socket))
                  for link in links} == expected, 'Bridge Geometry links changed.')
    return dict(obj=obj, mesh=obj.data, modifier=modifier, group=group,
                armature=modifiers[0], subsurf=modifiers[-1], token=token)


def _prove(obj, modifier, token):
    """Fresh enable proof: owned graph plus the tested source/configuration."""
    record = _owned_graph(obj, modifier, token)
    _source_configuration(obj, tuple(obj.modifiers))
    _require(not modifier.show_render and not modifier.show_in_editmode
             and not modifier.show_on_cage, 'Bridge render/edit flags changed.')
    _require(record['group'].animation_data is None,
             'Bridge node-group AnimationData is unsupported.')
    return record


def _same_components(record, fresh):
    return all(_identity(record[key]) == _identity(fresh[key])
               for key in ('obj', 'mesh', 'modifier', 'group', 'armature', 'subsurf'))


def _off(record):
    """Restore the retained owned modifier even after its graph was edited."""
    token = record['token']
    _ACTIVE.pop(token, None)
    try:
        modifier = record['modifier']
        for field in ('show_viewport', 'show_render', 'show_in_editmode', 'show_on_cage'):
            if getattr(modifier, field):
                setattr(modifier, field, False)
        _require(not any(getattr(modifier, field) for field in (
                 'show_viewport', 'show_render', 'show_in_editmode', 'show_on_cage')),
                 'Owned bridge did not disable.')
        return True
    except ReferenceError:
        # A removed modifier has no surviving viewport flag to restore.
        return True
    except Exception as error:
        _ACTIVE[token] = record
        _error(token, error)
        return False


def _off_all():
    # Include known but currently inactive components, without any scene scan.
    records = dict(_MANAGED)
    records.update(_ACTIVE)
    return all([_off(record) for record in tuple(records.values())])


def _recover_pending():
    """One metadata-only candidate scan after load/history, never in post hooks.

    Native validation must establish the subsequent graph callback boundary.
    A marked but altered graph is reported and never acquired or enabled.
    """
    global _RECOVERY_PENDING, _BUSY
    if not _RECOVERY_PENDING or _BUSY:
        return
    _BUSY = True
    try:
        candidates_by_token = {}
        for obj in bpy.data.objects:
            token = obj.get(_OWNER_KEY)
            if not isinstance(token, str) or len(token) != 32:
                continue
            candidates_by_token.setdefault(token, []).append(obj)
        for token, objects in candidates_by_token.items():
            try:
                _require(len(objects) == 1, 'Bridge token has multiple object owners.')
                obj = objects[0]
                candidates = [modifier for modifier in obj.modifiers
                              if modifier.type == 'NODES'
                              and modifier.node_group is not None
                              and modifier.node_group.get(_OWNER_KEY) == token]
                _require(len(candidates) == 1, 'Marked bridge has ambiguous modifiers.')
                # Off-only recognition deliberately does not require level 1,
                # custom-normals false, ADNone, or disabled render/edit flags.
                # A recovered own bridge must close even when enable is unsafe.
                fresh = _owned_graph(obj, candidates[0], token)
                previous = _MANAGED.get(token)
                _require(previous is None or _same_components(previous, fresh),
                         'Bridge ownership components changed during recovery.')
                _MANAGED[token] = fresh
                _off(fresh)
            except Exception as error:
                previous = _MANAGED.pop(token, None)
                if previous is not None:
                    _off(previous)
                _BLOCKED.add(token)
                _error(token, error)
        _RECOVERY_PENDING = False
    finally:
        _BUSY = False


def install_for_object(obj):
    """Create a disabled bridge for this explicit object; return its modifier.

    Only a still-proven bridge owned by this module may be reused. Construction
    failures remove only the new modifier/group and restore the new metadata.
    No register(), save, preference change, or playback is performed here.
    """
    global _BUSY
    _require(not _BUSY and not _PLAYING, 'Stop playback before installing a bridge.')
    _require(not any(_native_playing(scene) for scene in obj.users_scene),
             'Stop native playback before installing a bridge.')
    _recover_pending()
    token = obj.get(_OWNER_KEY)
    if token is not None:
        record = _MANAGED.get(token)
        _require(record is not None and record['obj'] == obj,
                 'Object already has an unacquired bridge marker.')
        fresh = _prove(obj, record['modifier'], token)
        _require(_same_components(record, fresh), 'Owned bridge components changed.')
        _require(_off(record), 'Owned bridge could not be disabled.')
        return record['modifier']
    _source_configuration(obj, tuple(obj.modifiers))
    _require(len(obj.modifiers) == 2, 'Install requires Armature then Subsurf only.')
    group = modifier = None
    token = uuid4().hex
    _BUSY = True
    try:
        group = bpy.data.node_groups.new('Playback Normal Bridge', 'GeometryNodeTree')
        group[_OWNER_KEY] = token
        group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        source = group.nodes.new('NodeGroupInput')
        remove = group.nodes.new('GeometryNodeRemoveAttribute')
        output = group.nodes.new('NodeGroupOutput')
        output.is_active_output = True
        remove.inputs['Name'].default_value = 'custom_normal'
        _require(remove.inputs['Pattern Mode'].default_value == 'Exact',
                 'Native Remove Attribute mode differs from the tested Exact default.')
        group.links.new(source.outputs['Geometry'], remove.inputs['Geometry'])
        group.links.new(remove.outputs['Geometry'], output.inputs['Geometry'])
        modifier = obj.modifiers.new('Playback Normal Bridge', 'NODES')
        modifier.show_viewport = False
        modifier.show_render = False
        modifier.show_in_editmode = False
        modifier.show_on_cage = False
        modifier.node_group = group
        obj.modifiers.move(len(obj.modifiers) - 1, 1)
        obj[_OWNER_KEY] = token
        record = _prove(obj, modifier, token)
        _MANAGED[token] = record
        return modifier
    except BaseException:
        if modifier is not None:
            obj.modifiers.remove(modifier)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
        if obj.get(_OWNER_KEY) == token:
            del obj[_OWNER_KEY]
        _MANAGED.pop(token, None)
        raise
    finally:
        _BUSY = False


def _enable_scene(scene):
    key = _scene_key(scene)
    for token, record in tuple(_MANAGED.items()):
        if token in _BLOCKED:
            continue
        try:
            if scene not in record['obj'].users_scene:
                continue
            fresh = _prove(record['obj'], record['modifier'], token)
            _require(_same_components(record, fresh), 'Owned bridge components changed.')
            record['scene_key'] = key
            _ACTIVE[token] = record  # Retain the recovery target before mutation.
            record['modifier'].show_viewport = True
            _require(record['modifier'].show_viewport, 'Owned bridge did not enable.')
        except Exception as error:
            _BLOCKED.add(token)
            _off(record)
            _error(token, error)


def _native_playing(scene):
    return any(window.scene == scene and window.screen is not None
               and window.screen.is_animation_playing
               for manager in bpy.data.window_managers for window in manager.windows)


@persistent
def _playback_pre(scene, *_unused):
    if not _REGISTERED or _BUSY:
        return
    _recover_pending()
    key = _scene_key(scene)
    _PLAYING[key] = scene
    for token, record in tuple(_MANAGED.items()):
        try:
            if scene in record['obj'].users_scene:
                _BLOCKED.discard(token)
        except ReferenceError:
            pass
    _enable_scene(scene)


@persistent
def _playback_post(scene, *_unused):
    # Retire the epoch before an RNA write can trigger graph callbacks.
    key = _scene_key(scene)
    _PLAYING.pop(key, None)
    for record in tuple(_ACTIVE.values()):
        if record.get('scene_key') == key:
            _off(record)


@persistent
def _guard(*_unused):
    if _BUSY:
        return
    if _RECOVERY_PENDING:
        _recover_pending()
    if not _ACTIVE:
        return
    for token, record in tuple(_ACTIVE.items()):
        if token in _BLOCKED:
            # A failed setter keeps the recovery reference. Later valid config
            # must not cancel the blocked epoch's pending off operation.
            _off(record)
            continue
        try:
            epoch_scene = _PLAYING.get(record.get('scene_key'))
            _require(epoch_scene is not None,
                     'Native playback epoch ended.')
            _require(epoch_scene in record['obj'].users_scene,
                     'Owned bridge moved out of the playing scene.')
            fresh = _prove(record['obj'], record['modifier'], token)
            _require(_same_components(record, fresh), 'Owned bridge components changed.')
        except Exception as error:
            _BLOCKED.add(token)
            _off(record)
            _error(token, error)


@persistent
def _graph_post(*_unused):
    if _BUSY:
        return
    if _RECOVERY_PENDING:
        _recover_pending()
    _guard()


@persistent
def _suspend(*_unused):
    # Save/render ends this epoch's optimization. Its post callback must not
    # resurrect it, including nested save/render and a Stop during either event.
    _BLOCKED.update(_ACTIVE)
    _off_all()


@persistent
def _invalidate_pre(*_unused):
    global _RECOVERY_PENDING, _BUSY
    previous_busy = _BUSY
    _BUSY = True
    try:
        _PLAYING.clear()
        _BLOCKED.clear()
        _off_all()
        # RNA will be invalidated; only primitive diagnostics survive the boundary.
        _ACTIVE.clear()
        _MANAGED.clear()
        # Suppress recovery during native history/load reconstruction. Only the
        # primitive post hook arms the next real frame/refresh boundary.
        _RECOVERY_PENDING = False
    finally:
        _BUSY = previous_busy


@persistent
def _invalidate_post(*_unused):
    global _RECOVERY_PENDING
    # Do not inspect even context/scene RNA in undo/redo post.
    _PLAYING.clear()
    _BLOCKED.clear()
    _ACTIVE.clear()
    _MANAGED.clear()
    _RECOVERY_PENDING = True


_HANDLERS = (
    ('animation_playback_pre', _playback_pre),
    ('animation_playback_post', _playback_post),
    ('frame_change_pre', _guard),
    ('depsgraph_update_post', _graph_post),
    ('save_pre', _suspend), ('render_pre', _suspend),
    ('load_pre', _invalidate_pre), ('load_post', _invalidate_post),
    ('undo_pre', _invalidate_pre), ('undo_post', _invalidate_post),
    ('redo_pre', _invalidate_pre), ('redo_post', _invalidate_post),
)


def register():
    """Register own handlers without installing anything or starting playback."""
    global _REGISTERED, _RECOVERY_PENDING
    if _REGISTERED:
        return
    if not _off_all():
        raise RuntimeError('Owned playback bridge could not disable before registration.')
    # A previous unregister may have been followed by a file change. Reacquire
    # persisted candidates fresh rather than comparing invalid old RNA pointers.
    _MANAGED.clear()
    added = []
    try:
        for event, function in _HANDLERS:
            handlers = getattr(bpy.app.handlers, event)
            if function not in handlers:
                handlers.insert(0, function)
                added.append((handlers, function))
    except BaseException:
        for handlers, function in reversed(added):
            handlers.remove(function)
        raise
    _REGISTERED = True
    _RECOVERY_PENDING = True


def unregister():
    """Turn owned bridges off; retain their disabled persistent components."""
    global _REGISTERED, _RECOVERY_PENDING
    _PLAYING.clear()
    _BLOCKED.clear()
    if _RECOVERY_PENDING:
        _recover_pending()
    if not _off_all():
        raise RuntimeError('Owned playback bridge cleanup failed; guards remain registered.')
    for event, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, event)
        while function in handlers:
            handlers.remove(function)
    _REGISTERED = False
    _RECOVERY_PENDING = False
    _ACTIVE.clear()
    # Keep installed ownership for an idempotent register/install in this module.
