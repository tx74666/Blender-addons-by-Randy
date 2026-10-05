"""Transactional, unbaked Hair preview through the external Wiggle Bones 1.1.2.

No physics code is bundled. The external extension must already be registered.
The supported sources are the unmodified files in the upstream 1.1.2 tag:
https://github.com/Hans-xwh/wiggle-bones/tree/1.1.2/src/wiggle_bones

Mapping v1: stiff=800*recovery**2, damp=20*damping, gravity in m/s^2 is divided
by |scene.gravity|*scene.unit_settings.scale_length, stretch is the native fraction,
mass is a relative
chain weight (default 1). Upstream applies stiff*dt**2/iterations and clamps
velocity retention to 1-damp*dt; these are solver coefficients, not measured
material properties. Native time uses scene.render.fps and ignores fps_base.
Bone count remains an independent Character Designer setting.

Only tail simulation is enabled. No bake, keys, Actions or NLA strips are
created. Save/undo/load, a timeline wrap/backward jump, mode/context changes,
and explicit stop end the session. Call stop_preview before rebind/export.
Unsupported/custom backends and pre-existing simulations are refused because
their in-flight state cannot be safely reconstructed by this adapter.
"""

import array
import hashlib
import math
from pathlib import Path
import sys
import time
import tomllib

import bpy
from bpy.app.handlers import persistent

from . import hair_bones_rig as hair
from . import hair_strand_registry as strands

BACKEND_VERSION = '1.1.2'
MAPPING_VERSION = 1
_HASHES = {
    '__init__.py': '7bd2830cbf7ded06ec695eb22ad66f2692dc6f70c84472e942fc33b3a7d001ca',
    'blender_manifest.toml': '15e6c4692fa33c94f16b085cfab6f769e8f74fc91773f8bb9b1e8c089933004f',
    'operators.py': '167983209e2cfa776587b8c861d0aec0b80afb16155bd2f843c336ff79f7d5c5',
    'physics_engine.py': '5bc5655396b2b94a0189a30a75b150017d60fc08674cfb9dc805f6832b2ef4e0',
    'properties.py': 'b24a6d00b65682a4501d90e49d0a754e6fbd203f328374b2434bad8b5c94a7d7',
    'wiggle_core.py': '0fa8a9fd8bbed0bc957676695755cb0e6e79956469c0049a1a3bf738c7f37929',
    'wiggle_ui.py': 'd37b098f103cc02898ab382c6839784507508290a4492571a22ff6f74de801b6',
}
_SESSION = None
_LAST_REASON = ''
_STOPPING = False
_GUARDS_REGISTERED = False
_FRAME_OWNERSHIP = {}
_AVAILABLE_CACHE = None
_AVAILABLE_CACHE_TIME = 0.0
_TIMER_RUNNING = False
_CHANNELS = ('location', 'rotation_euler', 'rotation_quaternion',
             'rotation_axis_angle', 'scale')
_OBJECT_CHANNELS = _CHANNELS + ('delta_location', 'delta_rotation_euler',
                              'delta_rotation_quaternion', 'delta_scale')
_TRANSFORMS = frozenset(_CHANNELS + ('matrix', 'matrix_basis', 'matrix_channel',
    'rotation_mode', 'head', 'tail', 'roll', 'head_local', 'tail_local',
    'matrix_local', 'inherit_scale', 'use_inherit_rotation', 'use_local_location'))


class HairWiggleError(ValueError):
    """Preview was refused or rolled back without baking author data."""


def _fail(message):
    raise HairWiggleError(message)


def _backend():
    candidates = []
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if not path or name.endswith(('.properties', '.physics_engine')):
            continue
        root = Path(path).parent
        manifest = root / 'blender_manifest.toml'
        if not manifest.is_file():
            continue
        try:
            metadata = tomllib.loads(manifest.read_text(encoding='utf8'))
        except (OSError, ValueError):
            continue
        if metadata.get('id') == 'wiggle_bones' and hasattr(module, 'properties'):
            candidates.append((name, module, root, metadata))
    if len(candidates) != 1:
        _fail('Enable exactly one official Wiggle Bones 1.1.2 extension first.')
    name, module, root, metadata = candidates[0]
    if metadata.get('version') != BACKEND_VERSION:
        _fail('Only the verified Wiggle Bones 1.1.2 schema is supported.')
    for filename, expected in _HASHES.items():
        try:
            payload = (root / filename).read_bytes().replace(b'\r\n', b'\n')
        except OSError as exc:
            raise HairWiggleError('Wiggle 1.1.2 source files are unavailable.') from exc
        if hashlib.sha256(payload).hexdigest() != expected:
            _fail('Wiggle source differs from the official 1.1.2 tag: ' + filename)
    properties = module.properties
    for owner, typename in ((bpy.types.Scene, 'WiggleScene'),
                            (bpy.types.Object, 'WiggleObject'),
                            (bpy.types.PoseBone, 'WiggleBoneSettings')):
        prop = owner.bl_rna.properties.get('wiggle')
        expected = getattr(properties, typename, None)
        if (prop is None or prop.type != 'POINTER' or expected is None
                or prop.fixed_type.identifier != expected.bl_rna.identifier
                or not getattr(expected, 'is_registered', False)):
            _fail('The registered .wiggle RNA schema does not match 1.1.2.')
    engine = getattr(module, 'physics_engine', None)
    core = sys.modules.get(name + '.wiggle_core')
    if engine is None or core is None or not callable(getattr(core, 'build_list', None)):
        _fail('Wiggle core is not fully loaded.')
    for collection, function in ((bpy.app.handlers.frame_change_pre, engine.wiggle_pre),
                                 (bpy.app.handlers.frame_change_post, engine.wiggle_post)):
        if collection.count(function) != 1:
            _fail('Wiggle frame callbacks must be registered exactly once.')
    return {'name': name, 'module': module, 'core': core, 'engine': engine}


def available(context=None):
    """Read-only backend availability; does not import or enable an extension."""
    global _AVAILABLE_CACHE, _AVAILABLE_CACHE_TIME
    now = time.monotonic()
    if _AVAILABLE_CACHE is not None and now - _AVAILABLE_CACHE_TIME < 1.0:
        return dict(_AVAILABLE_CACHE)
    try:
        backend = _backend()
        result = {'available': True, 'version': BACKEND_VERSION,
                'module': backend['name'], 'reason': '', 'mapping_version': MAPPING_VERSION}
    except (HairWiggleError, AttributeError, ReferenceError) as exc:
        result = {'available': False, 'version': None, 'module': None, 'reason': str(exc),
                'mapping_version': MAPPING_VERSION}
    _AVAILABLE_CACHE, _AVAILABLE_CACHE_TIME = result, now
    return dict(result)


def status(context=None):
    session = _SESSION
    return {'active': session is not None, 'source': session['source'].name if session else None,
            'strand_ids': list(session['ids']) if session else [],
            'started_playback': bool(session and session['started_playback']),
            'reason': _LAST_REASON, 'version': BACKEND_VERSION, 'mapping_version': MAPPING_VERSION}


def _clone(value):
    """Keep native ID pointers and IDProperty array storage, without callbacks."""
    if isinstance(value, bpy.types.ID):
        return value
    if hasattr(value, 'keys'):
        return {key: _clone(value[key]) for key in value.keys()}
    if hasattr(value, 'typecode'):
        return array.array(value.typecode, value)
    if isinstance(value, (tuple, list)):
        return [_clone(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    _fail('Unsupported saved Wiggle property value; preview cannot restore it safely.')


def _raw_snapshot(owner):
    # Blender 5.1 stores dynamic RNA PropertyGroups separately from owner ID
    # properties: owner['wiggle'] is unrelated and must never be used here.
    if not owner.is_property_set('wiggle'):
        return owner, False, None
    group = owner.wiggle
    allowed = set(group.bl_rna.properties.keys()) - {'rna_type'}
    if set(group.keys()) - allowed:
        _fail('Custom Wiggle properties prevent an exact 1.1.2 rollback.')
    return owner, True, _clone(group)


def _restore_raw(snapshot):
    owner, exists, value = snapshot
    if exists:
        group = owner.wiggle
        for key in list(group.keys()):
            del group[key]
        for key, item in value.items():
            group[key] = _clone(item)
    else:
        owner.property_unset('wiggle')


def _channels(owner, object_channels=False):
    names = _OBJECT_CHANNELS if object_channels else _CHANNELS
    return owner, owner.rotation_mode, {name: tuple(getattr(owner, name)) for name in names}


def _restore_channels(snapshot):
    owner, mode, channels = snapshot
    owner.rotation_mode = mode
    for name, value in channels.items():
        setattr(owner, name, value)


def _pose_custom(owner):
    """Numeric pose switches only; never profile/ownership JSON or ID pointers."""
    result = {}
    for key in owner.keys():
        value = owner[key]
        if (isinstance(value, (bool, int, float))
                or getattr(value, 'typecode', None) in {'i', 'f', 'd'}):
            result[key] = _clone(value)
    return owner, result


def _restore_pose_custom(snapshot):
    owner, values = snapshot
    for key, value in values.items():
        owner[key] = _clone(value)


def _array_proof(value):
    return tuple(_array_proof(item) if hasattr(item, '__iter__') else item for item in value)


def _rna_snapshot(value, depth=0):
    """Immutable Action data proof including layered channelbags and modifiers."""
    if depth > 12:
        _fail('Animation data exceeds the supported rollback proof depth.')
    result = []
    for prop in value.bl_rna.properties:
        if prop.identifier == 'rna_type' or prop.identifier in {
                'users', 'tag', 'is_updated', 'is_updated_data', 'is_updated_transform',
                'is_valid', 'is_evaluated', 'session_uid'}:
            continue
        # Dynamic RNA PG pointer getters initialize backing data even for an
        # unset ID pointer in Blender 5.1. A proof must never materialize it.
        if (prop.type == 'POINTER' and isinstance(value, bpy.types.PropertyGroup)
                and not value.is_property_set(prop.identifier)):
            result.append((prop.identifier, None))
            continue
        try:
            item = getattr(value, prop.identifier)
        except (AttributeError, RuntimeError):
            continue  # Legacy accessors on a layered Action can be unavailable.
        if prop.type == 'COLLECTION':
            item = tuple(_rna_snapshot(child, depth + 1) for child in item)
        elif prop.type == 'POINTER':
            if isinstance(item, bpy.types.ID):
                item = (item.as_pointer(), item.name_full)
            elif isinstance(item, bpy.types.PropertyGroup):
                item = _rna_snapshot(item, depth + 1)
            elif item is not None:
                # IDProperty/RNA backreferences are identity, not owned Action data.
                item = (item.bl_rna.identifier, getattr(item, 'name', ''))
        elif getattr(prop, 'is_array', False):
            item = _array_proof(item)
        elif prop.type == 'ENUM' and isinstance(item, set):
            item = tuple(sorted(item))
        result.append((prop.identifier, item))
    return tuple(result)


def _animation_proof():
    # No Action copying or mutation: preserve the original datablock identity.
    actions = tuple((action, _rna_snapshot(action),
                    {key: _clone(action[key]) for key in action.keys()}) for action in bpy.data.actions)
    owners = [bpy.context.scene] + list(bpy.context.scene.objects)
    owners += [ob.data.shape_keys for ob in bpy.context.scene.objects
               if ob.type == 'MESH' and ob.data.shape_keys]
    associations = tuple((owner, _rna_snapshot(owner.animation_data)
                          if owner.animation_data else None) for owner in owners)
    return actions, associations


def _animation_same(proof):
    actions, associations = proof
    return (len(actions) == len(bpy.data.actions)
            and all(action.name in bpy.data.actions and _rna_snapshot(action) == data
                    and {key: _clone(action[key]) for key in action.keys()} == custom
                    for action, data, custom in actions)
            and all((_rna_snapshot(owner.animation_data) if owner.animation_data else None) == saved
                    for owner, saved in associations))


def _driver_paths(owner):
    data = getattr(owner, 'animation_data', None)
    return tuple(curve.data_path for curve in data.drivers) if data else ()


def _animated_paths(owner):
    data = getattr(owner, 'animation_data', None)
    if not data:
        return ()
    actions = [data.action] if data.action else []
    actions += [strip.action for track in data.nla_tracks for strip in track.strips if strip.action]
    paths = []
    for action in actions:
        try:
            paths.extend(curve.data_path for curve in action.fcurves)
        except (AttributeError, RuntimeError):
            pass
        for layer in getattr(action, 'layers', ()):
            for strip in layer.strips:
                for bag in getattr(strip, 'channelbags', ()):
                    paths.extend(curve.data_path for curve in bag.fcurves)
    return tuple(paths)


def _hair_dependencies(armature, bones):
    prefixes = tuple(bone.path_from_id() for bone in bones)
    data_prefixes = tuple(bone.bone.path_from_id() for bone in bones)
    if any(bone.constraints for bone in bones):
        _fail('Hair transform constraints must be removed before Wiggle preview.')
    ob = armature
    seen = set()
    while ob is not None:
        if ob.as_pointer() in seen:
            _fail('Armature object parenting contains a cycle.')
        seen.add(ob.as_pointer())
        if ob.constraints:
            _fail('Armature/Object parent constraints need a separate feedback proof before preview.')
        object_transforms = frozenset(_OBJECT_CHANNELS + ('rotation_mode', 'matrix_world',
                                       'matrix_local', 'matrix_basis', 'parent', 'parent_type', 'parent_bone'))
        if any(path.split('.', 1)[0].split('[', 1)[0] in object_transforms
               for path in _driver_paths(ob)):
            _fail('Armature/Object parent transform drivers may feed back from Hair simulation.')
        ob = ob.parent
    for path in _driver_paths(armature):
        if path.startswith('wiggle') or any(path.startswith(prefix + '.')
                                             for prefix in prefixes):
            _fail('Hair transform/settings drivers compete with Wiggle preview.')
    for path in _driver_paths(armature.data):
        if any(path.startswith(prefix) for prefix in data_prefixes):
            _fail('Hair Rest/parent drivers invalidate the saved ownership proof.')
    for path in _animated_paths(armature) + _driver_paths(armature):
        if path.startswith('wiggle') or any(path.startswith(prefix + '.wiggle') for prefix in prefixes):
            _fail('Animated Wiggle settings cannot be temporarily overridden.')


def register_frame_callback_proof(function, armature, bone_names):
    """Declare a reviewed callback's complete pose-write ownership.

    For integration code only: this is an explicit caller proof, not an inferred
    guarantee from a Python function name. A callback with unknown/global write
    ownership must not be declared here. It must not write Actions, NLA or Wiggle
    settings. Empty names declares a callback that does not write pose channels.
    """
    if not callable(function) or not isinstance(armature, bpy.types.Object) or armature.type != 'ARMATURE':
        _fail('A callback proof needs the exact callable and its Armature.')
    names = frozenset(bone_names)
    if any(name not in armature.pose.bones for name in names):
        _fail('Callback ownership references missing pose bones.')
    _FRAME_OWNERSHIP[function] = (armature, names)


def unregister_frame_callback_proof(function):
    _FRAME_OWNERSHIP.pop(function, None)


def _handler_gate(backend, armature, hair_names):
    known = {backend['engine'].wiggle_pre, backend['engine'].wiggle_post, _frame_guard}
    # Exact exported callables, not __name__/module-name matching. The reviewed
    # body callback changes display bindings/membership and excludes owned Hair.
    # The ordinary forearm callback updates owned corrective mesh Shape Keys.
    for module_name, function_name in (('bone_collections', '_frame_visibility'),
                                        ('forearm_twist', '_frame_post')):
        module = sys.modules.get(__package__ + '.' + module_name)
        function = getattr(module, function_name, None)
        if function:
            if module_name == 'forearm_twist' and function in bpy.app.handlers.frame_change_post:
                # Pending recovery/cleanup can also restore a saved hand-test
                # pose. Require the normal runtime path before accepting it.
                if (getattr(module, '_INITIALIZE_PENDING', True)
                        or getattr(module, '_SCENE_CLEANUP_PENDING', True)):
                    _fail('Finish pending Forearm preview recovery before starting Hair preview.')
            known.add(function)
    for collection in (bpy.app.handlers.frame_change_pre, bpy.app.handlers.frame_change_post):
        for function in collection:
            if function in known:
                continue
            proof = _FRAME_OWNERSHIP.get(function)
            if proof and (proof[0] != armature or not proof[1].intersection(hair_names)):
                continue
            _fail('An unverified frame callback may modify this Hair pose or Actions; '
                  'provide reviewed disjoint pose-write ownership first.')


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(name + ' must be a finite number.')
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        _fail(name + ' is outside its supported range.')
    return value


def _profile(settings):
    if not isinstance(settings, dict):
        _fail('Pass effective settings for each selected strand.')
    base = {key: _number(settings.get(key), key, 0, high) for key, high in
            (('recovery', 1), ('damping', 1), ('gravity', 20), ('stretch', 1))}
    knots = settings.get('depth', [])
    if not isinstance(knots, list):
        _fail('Depth settings must be normalized knot dictionaries.')
    normalized = []
    for knot in knots:
        if not isinstance(knot, dict):
            _fail('Depth settings must be normalized knot dictionaries.')
        item = {'position': _number(knot.get('position'), 'depth.position', 0, 1)}
        for key, high in (('recovery', 1), ('damping', 1), ('gravity', 20), ('mass', 10)):
            item[key] = _number(knot.get(key, base.get(key, 1)), 'depth.' + key,
                                0.01 if key == 'mass' else 0, high)
        if normalized and item['position'] <= normalized[-1]['position']:
            _fail('Depth knot positions must be strictly increasing.')
        normalized.append(item)
    return base, normalized


def _depth_values(profile, position):
    base, knots = profile
    result = dict(base, mass=1.0)
    if not knots:
        return result
    first, second = knots[0], knots[-1]
    if position <= first['position']:
        second = first
    elif position >= second['position']:
        first = second
    else:
        first, second = next((a, b) for a, b in zip(knots, knots[1:])
                             if a['position'] <= position <= b['position'])
    span = second['position'] - first['position']
    factor = (position - first['position']) / span if span else 0
    for key in ('recovery', 'damping', 'gravity', 'mass'):
        result[key] = first[key] + (second[key] - first[key]) * factor
    return result


def _preflight(context, source, registry, profiles, strand_ids, backend):
    if _SESSION is not None:
        _fail('Stop the existing Hair preview before starting another stage.')
    if context.scene != bpy.context.scene or context.mode not in {'OBJECT', 'POSE'}:
        _fail('Start preview in the active Scene in Object or Pose Mode.')
    fresh = strands.read(source, validate=True)
    if registry != fresh:
        _fail('The supplied strand registry is stale; refresh its strict ownership proof.')
    armature = source.get(hair.RIG_KEY)
    if (armature.name not in context.scene.objects or armature.data.pose_position != 'POSE'
            or source.library or source.override_library or armature.library
            or armature.override_library or armature.data.library or armature.data.users != 1):
        _fail('Preview needs a local single-user Armature in Pose position in this Scene.')
    ids = tuple(strand_ids)
    by_id = {item['strand_id']: item for item in fresh['strands']}
    if not ids or len(set(ids)) != len(ids) or set(ids) - set(by_id):
        _fail('Choose one or more unique registry-owned Hair strands.')
    if not isinstance(profiles, dict) or any(key not in profiles for key in ids):
        _fail('Pass a strand_id → effective settings mapping.')
    effective = {key: _profile(profiles[key]) for key in ids}
    bones = [armature.pose.bones[name] for item in fresh['strands'] for name in item['bones']]
    _hair_dependencies(armature, bones)
    _handler_gate(backend, armature, {bone.name for bone in bones})
    scene = context.scene
    if scene.render.fps_base != 1.0 or scene.render.fps < 1:
        _fail('Wiggle 1.1.2 requires fps_base=1 for the supported time mapping.')
    if any(path.startswith('wiggle') for path in _driver_paths(scene) + _animated_paths(scene)):
        _fail('Animated Scene Wiggle settings prevent a safe temporary preview.')
    if scene.is_property_set('wiggle') and any(getattr(scene.wiggle, key)
            for key in ('is_rendering', 'reset', 'is_preroll', 'syncing')):
        _fail('Finish the current Wiggle render/reset/preroll operation first.')
    unit_scale = scene.unit_settings.scale_length
    gravity_length = scene.gravity.length
    if not math.isfinite(unit_scale) or unit_scale <= 0 or not math.isfinite(gravity_length):
        _fail('Scene length units and gravity must be finite and usable.')
    if gravity_length <= 1e-8 and any(base['gravity'] or any(knot['gravity'] for knot in knots)
                                    for base, knots in effective.values()):
        _fail('Nonzero Hair gravity needs a nonzero Scene gravity vector; the author vector is preserved.')
    if abs(armature.matrix_world.determinant()) < 1e-10:
        _fail('A zero Armature world scale prevents native world-space simulation.')
    for ob in scene.objects:
        if ob.type != 'ARMATURE':
            continue
        if ob.library or ob.override_library or ob.mode == 'EDIT':
            _fail('Global Wiggle rebuild requires local Armatures outside Edit Mode.')
        for bone in ob.pose.bones:
            if bone.is_property_set('wiggle') and (bone.wiggle.head or bone.wiggle.tail):
                _fail('Stop every existing Wiggle simulation before the CD preview.')
        if any(path.startswith('wiggle') or '.wiggle' in path
               for path in _driver_paths(ob) + _animated_paths(ob)):
            _fail('Another Armature has animated Wiggle settings.')
    return armature, fresh, ids, by_id, effective


def _bone_proof(bone):
    result = hair._bone_state(bone)
    result.update(deform=bone.use_deform, inherit_scale=bone.inherit_scale,
                  inherit_rotation=bone.use_inherit_rotation, local_location=bone.use_local_location,
                  owner=bone.get(hair.OWNER_KEY), source=bone.get(hair.SOURCE_KEY),
                  signature=bone.get(hair.SIGNATURE_KEY))
    return result


def _snapshot(context, source, armature, registry, ids, backend):
    scene = context.scene
    raw = [_raw_snapshot(scene)]
    channels = []
    custom = [_pose_custom(scene)]
    shapes = []
    for ob in scene.objects:
        channels.append(_channels(ob, True))
        custom.append(_pose_custom(ob))
        if ob.type == 'MESH' and ob.data.shape_keys:
            keys = ob.data.shape_keys
            shapes.append((keys, keys.eval_time, [(block, block.value) for block in keys.key_blocks]))
        if ob.type == 'ARMATURE':
            raw.append(_raw_snapshot(ob))
            for bone in ob.pose.bones:
                raw.append(_raw_snapshot(bone))
                channels.append(_channels(bone))
                custom.append(_pose_custom(bone))
    return {'scene': scene, 'source': source, 'armature': armature, 'registry': registry,
        'ids': ids, 'backend': backend, 'raw': raw, 'channels': channels,
        'custom': custom, 'shapes': shapes,
        'actions': _animation_proof(), 'frame': scene.frame_current,
        'subframe': scene.frame_subframe, 'autokey': scene.tool_settings.use_keyframe_insert_auto,
        'screen': context.screen, 'window': context.window, 'view_layer': context.view_layer,
        'was_playing': bool(context.screen and context.screen.is_animation_playing),
        'started_playback': False, 'lastframe': scene.frame_current,
        'registry_raw': source.get(strands.REGISTRY_KEY),
        'rig_record': source.get(hair.RECORD_KEY), 'rig_pointer': source.get(hair.RIG_KEY),
        'source_counts': (len(source.data.vertices), len(source.data.edges), len(source.data.polygons)),
        'bone_count': len(armature.data.bones),
        'unit_scale': scene.unit_settings.scale_length, 'gravity': tuple(scene.gravity),
        'rest': [(bone, _bone_proof(bone)) for item in registry['strands']
                 for bone in (armature.data.bones[name] for name in item['bones'])],
        'anchors': [(armature.data.bones[item['anchor']['name']],
                     _bone_proof(armature.data.bones[item['anchor']['name']]))
                    for item in registry['strands'] if item['anchor']]}


def _configure(session, by_id, effective):
    scene, armature = session['scene'], session['armature']
    armature.wiggle.freeze = True
    scene.wiggle.auto_sync = False
    scene.tool_settings.use_keyframe_insert_auto = False
    armature.wiggle['use_pin'] = False
    armature.wiggle['mute'] = False
    scene.wiggle.loop = False
    scene.wiggle.full_bone_collision.enable_fullbone_collision = False
    # These booleans normally rebuild the *whole Scene*. Native raw backing
    # writes batch them; one official build_list derives enable/list/runtime.
    for item in session['registry']['strands']:
        selected = item['strand_id'] in session['ids']
        total = sum((armature.data.bones[name].tail_local -
                     armature.data.bones[name].head_local).length for name in item['bones'])
        if not math.isfinite(total) or total <= 1e-8:
            _fail('A selected Hair chain has invalid Rest length.')
        distance = 0.0
        for name in item['bones']:
            bone = armature.pose.bones[name]
            bone.wiggle['head'] = False
            bone.wiggle['tail'] = selected
            bone.wiggle['mute'] = False
            bone.wiggle['head_mute'] = False
            bone.wiggle['tail_mute'] = False
            if not selected:
                continue
            distance += (bone.bone.tail_local - bone.bone.head_local).length
            values = _depth_values(effective[item['strand_id']], distance / total)
            bone.wiggle.stiff = 800.0 * values['recovery'] ** 2
            bone.wiggle.damp = 20.0 * values['damping']
            # reset_bone stores endpoints through matrix_world, so world scale
            # is already represented. Applying it again would double scale gravity.
            native_acceleration = scene.gravity.length * scene.unit_settings.scale_length
            bone.wiggle.gravity = values['gravity'] / native_acceleration if native_acceleration else 0.0
            bone.wiggle.stretch = values['stretch']
            bone.wiggle.mass = values['mass']
            bone.wiggle.chain = True
            bone.wiggle.wind_ob = None
            bone.wiggle.collider = None
            bone.wiggle.collider_collection = None
            bone.wiggle.pin_target = None
    scene.wiggle.enable = True  # Its official callback performs the one rebuild.
    scene.wiggle.lastframe = scene.frame_current
    armature.wiggle.freeze = False


def start_preview(context, source, registry, profiles, strand_ids):
    """Start one stage; profiles is an effective mapping keyed by strand_id."""
    global _SESSION, _LAST_REASON
    backend = _backend()
    armature, fresh, ids, by_id, effective = _preflight(
        context, source, registry, profiles, strand_ids, backend)
    session = _snapshot(context, source, armature, fresh, ids, backend)
    _SESSION = session
    try:
        register_guards()
        _configure(session, by_id, effective)
        if not bpy.app.timers.is_registered(_mode_timer):
            bpy.app.timers.register(_mode_timer, first_interval=0.1)
        if context.screen and context.window and not session['was_playing']:
            with context.temp_override(window=context.window, screen=context.screen):
                result = bpy.ops.screen.animation_play()
            session['started_playback'] = bool(context.screen.is_animation_playing)
            if 'FINISHED' not in result or not context.screen.is_animation_playing:
                _fail('Blender did not start timeline playback.')
        _LAST_REASON = ''
        return status(context)
    except Exception as exc:
        try:
            stop_preview(context, reason='Start failed; restored the original author state.')
        except Exception as rollback:
            raise HairWiggleError('Start failed and rollback needs attention: ' + str(rollback)) from exc
        if isinstance(exc, HairWiggleError):
            raise
        raise HairWiggleError('Wiggle preview start failed: ' + str(exc)) from exc


def stop_preview(context=None, *, reason='Stopped'):
    """Freeze first, restore author state, cancel only playback we started."""
    global _SESSION, _LAST_REASON, _STOPPING
    session = _SESSION
    if session is None or _STOPPING:
        return status(context)
    _STOPPING = True
    errors = []
    try:
        # Neither field invokes build_list. Disable before restoring the frame,
        # so upstream frame handlers cannot clear any author bone channels.
        try:
            session['armature'].wiggle.freeze = True
            session['scene'].wiggle['enable'] = False
        except (ReferenceError, RuntimeError, TypeError) as exc:
            errors.append(str(exc))
        _SESSION = None
        if not _TIMER_RUNNING and bpy.app.timers.is_registered(_mode_timer):
            bpy.app.timers.unregister(_mode_timer)
        screen, window = session['screen'], session['window']
        if session['started_playback'] and screen and window and screen.is_animation_playing:
            try:
                with bpy.context.temp_override(window=window, screen=screen):
                    bpy.ops.screen.animation_cancel(restore_frame=False)
            except (ReferenceError, RuntimeError, TypeError) as exc:
                errors.append(str(exc))
        scene = session['scene']
        try:
            with bpy.context.temp_override(scene=scene, view_layer=session['view_layer']):
                scene.frame_set(session['frame'], subframe=session['subframe'])
        except (ReferenceError, RuntimeError, TypeError) as exc:
            errors.append(str(exc))
        for snapshot in session['custom']:
            try:
                _restore_pose_custom(snapshot)
            except (ReferenceError, RuntimeError, TypeError) as exc:
                errors.append(str(exc))
        for keys, eval_time, values in session['shapes']:
            try:
                keys.eval_time = eval_time
                for block, value in values:
                    block.value = value
            except (ReferenceError, RuntimeError, TypeError) as exc:
                errors.append(str(exc))
        for snapshot in session['channels']:
            try:
                _restore_channels(snapshot)
            except (ReferenceError, RuntimeError, TypeError) as exc:
                errors.append(str(exc))
        scene.tool_settings.use_keyframe_insert_auto = session['autokey']
        for snapshot in reversed(session['raw']):
            try:
                _restore_raw(snapshot)
            except (ReferenceError, RuntimeError, TypeError, ValueError) as exc:
                errors.append(str(exc))
        session['view_layer'].update()
        if not _animation_same(session['actions']):
            errors.append('Action data changed externally during preview; external edits were preserved.')
        _LAST_REASON = reason
        if errors:
            _LAST_REASON += ' ' + '; '.join(errors)
            raise HairWiggleError(_LAST_REASON)
        return status(context)
    finally:
        _STOPPING = False


def _changed(session):
    source, armature = session['source'], session['armature']
    if (bpy.context.scene != session['scene'] or bpy.context.mode not in {'OBJECT', 'POSE'}
            or armature.mode == 'EDIT' or source.mode == 'EDIT'):
        return 'Scene or mode changed.'
    if (source.get(strands.REGISTRY_KEY) != session['registry_raw']
            or source.get(hair.RECORD_KEY) != session['rig_record']
            or source.get(hair.RIG_KEY) != session['rig_pointer']
            or (len(source.data.vertices), len(source.data.edges), len(source.data.polygons))
                != session['source_counts']):
        return 'Hair binding or registry changed.'
    if (len(armature.data.bones) != session['bone_count']
            or any(_bone_proof(bone) != saved for bone, saved in session['rest'] + session['anchors'])):
        return 'Hair Rest or parent relationships changed.'
    if (session['scene'].unit_settings.scale_length != session['unit_scale']
            or tuple(session['scene'].gravity) != session['gravity']):
        return 'Scene gravity or length units changed.'
    bones = [armature.pose.bones[bone.name] for bone, _ in session['rest']]
    _hair_dependencies(armature, bones)
    _handler_gate(session['backend'], armature, {bone.name for bone in bones})
    return ''


@persistent
def _frame_guard(scene, *args):
    session = _SESSION
    if not session or _STOPPING:
        return
    try:
        reason = _changed(session)
        frame = scene.frame_current
        if scene != session['scene']:
            reason = 'Scene changed.'
        elif frame < session['lastframe'] or frame - session['lastframe'] > 4:
            reason = 'Timeline wrapped, moved backwards or skipped more than four frames.'
        if reason:
            stop_preview(reason=reason)
        else:
            session['lastframe'] = frame
    except Exception as exc:
        stop_preview(reason='Preview guard stopped: ' + str(exc))


@persistent
def _lifecycle_guard(*args):
    if _SESSION:
        stop_preview(reason='Save, undo, redo, load or render ended Hair preview.')


def _mode_timer():
    global _TIMER_RUNNING
    if not _GUARDS_REGISTERED or _SESSION is None:
        return None
    _TIMER_RUNNING = True
    try:
        try:
            reason = _changed(_SESSION)
            if reason:
                stop_preview(reason=reason)
        except Exception as exc:
            stop_preview(reason='Preview guard stopped: ' + str(exc))
    finally:
        _TIMER_RUNNING = False
    return 0.1 if _SESSION else None


_LIFECYCLE_LISTS = ('save_pre', 'undo_pre', 'redo_pre', 'load_pre', 'render_pre')


def register_guards():
    """Idempotently register only CD-owned guards; never change Wiggle handlers."""
    global _GUARDS_REGISTERED
    if _frame_guard not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.insert(0, _frame_guard)
    for name in _LIFECYCLE_LISTS:
        collection = getattr(bpy.app.handlers, name)
        if _lifecycle_guard not in collection:
            collection.insert(0, _lifecycle_guard)
    _GUARDS_REGISTERED = True


def unregister_guards():
    global _GUARDS_REGISTERED
    stop_preview(reason='Character Designer unregistered.')
    _GUARDS_REGISTERED = False
    if bpy.app.timers.is_registered(_mode_timer):
        bpy.app.timers.unregister(_mode_timer)
    for name, function in [('frame_change_pre', _frame_guard)] + [
            (name, _lifecycle_guard) for name in _LIFECYCLE_LISTS]:
        collection = getattr(bpy.app.handlers, name)
        while function in collection:
            collection.remove(function)
    _FRAME_OWNERSHIP.clear()
