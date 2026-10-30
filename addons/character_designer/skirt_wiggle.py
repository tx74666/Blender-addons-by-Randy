"""Owned Dress Automatic/Manual switching through registered Wiggle 2.

The external solver's files and registration remain unchanged. Only synchronous
RNA setter batches suppress its UI propagation callback and restore it in finally.
Only the existing PHYS chains are simulated. Manual freezes their current
local pose and keeps the native DEF delta enabled, preserving the visible
skirt and every manual wire input. Save/load reseed Automatic from that pose.
Original editing and export deliberately choose Manual before their snapshot.
"""

import copy
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from types import ModuleType
import uuid

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix

from . import skirt_rig as skirt

STATE_KEY = 'character_designer_skirt_wiggle_v1'
STATE_SCENE_KEY = 'character_designer_skirt_wiggle_scene_v1'
SCENE_ID_KEY = 'character_designer_skirt_wiggle_scene_id_v1'
BACKEND_NAME = 'Wiggle 2'
BACKEND_VERSION = '2.2.4'
# Verified installed source, normalized LF. No third-party solver is bundled.
BACKEND_SHA256 = '7333687ae3b6fd20feae8afda649faed1d99bc7b98e92622a8993c09061deac1'
_SESSIONS = {}
_SAVE_RESUME = []
_REGISTERED = False
_IN_TIMER = False
_WRITE_DEPTH = 0
_BACKEND_CACHE = None
_BACKEND_CACHE_TIME = 0.0
_TEARDOWN_REASON = None
_HISTORY_RECOVERY_PENDING = False
_CHANNELS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')
_DEFAULTS = {'wiggle_stiff': 400.0, 'wiggle_damp': 8.0, 'wiggle_gravity': 0.25,
             'wiggle_mass': 1.0, 'wiggle_stretch': 0.0, 'wiggle_chain': True}
_FLAGS = ('wiggle_enable', 'wiggle_head', 'wiggle_tail', 'wiggle_mute',
          'wiggle_head_mute', 'wiggle_tail_mute')


class SkirtWiggleError(ValueError):
    """A refused or rolled-back Dress motion change."""


def _fail(message):
    raise SkirtWiggleError(message)


def _backend():
    candidates = []
    for module in tuple(sys.modules.values()):
        if not isinstance(module, ModuleType):
            continue
        # bpy.ops and other dynamic namespaces live in sys.modules too. Avoid
        # invoking their attribute resolution while identifying add-on modules.
        metadata = module.__dict__.get('bl_info')
        if (isinstance(metadata, dict) and metadata.get('name') == BACKEND_NAME
                and callable(module.__dict__.get('reset_bone'))):
            candidates.append(module)
    if len(candidates) != 1:
        _fail('Enable the verified Wiggle 2 2.2.4 add-on to use Automatic Dress motion.')
    module = candidates[0]
    if tuple(module.bl_info.get('version', ())) != (2, 2, 4):
        _fail('Automatic Dress motion currently supports Wiggle 2 2.2.4.')
    try:
        payload = Path(module.__file__).read_bytes().replace(b'\r\n', b'\n')
    except (OSError, TypeError, AttributeError) as error:
        raise SkirtWiggleError('The registered Wiggle source is unavailable.') from error
    if hashlib.sha256(payload).hexdigest() != BACKEND_SHA256:
        _fail('The registered Wiggle 2 source differs from the verified 2.2.4 backend.')
    for owner, name in ((bpy.types.Scene, 'wiggle_enable'), (bpy.types.Object, 'wiggle_freeze'),
                        (bpy.types.Object, 'wiggle_mute'), (bpy.types.PoseBone, 'wiggle_tail'),
                        (bpy.types.PoseBone, 'wiggle_head'), (bpy.types.PoseBone, 'wiggle_enable')):
        prop = owner.bl_rna.properties.get(name)
        if prop is None or prop.type != 'BOOLEAN':
            _fail('The registered Wiggle 2 schema is incomplete; enable the add-on again.')
    schema = ((bpy.types.Scene, 'WiggleScene'), (bpy.types.Object, 'WiggleObject'),
              (bpy.types.PoseBone, 'WiggleBone'))
    # Official disable leaves PG PointerRNA behind. Its fixed_type target can
    # already be freed, so authenticate all classes before reading any target.
    if any(not getattr(getattr(module, name, None), 'is_registered', False)
           for _owner, name in schema):
        _fail('Wiggle 2 is disabled or its RNA classes are not registered.')
    for owner, typename in schema:
        prop = owner.bl_rna.properties.get('wiggle')
        expected = getattr(module, typename)
        if (prop is None or prop.type != 'POINTER'
                or prop.fixed_type.identifier != expected.bl_rna.identifier):
            _fail('The registered Wiggle data schema differs from the verified backend.')
    if (bpy.app.handlers.frame_change_pre.count(module.wiggle_pre) != 1
            or bpy.app.handlers.frame_change_post.count(module.wiggle_post) != 1):
        _fail('Wiggle 2 frame callbacks must be registered exactly once.')
    return module


def available():
    """Cached read-only UI availability; actions always repeat the full proof."""
    global _BACKEND_CACHE, _BACKEND_CACHE_TIME
    now = time.monotonic()
    if _BACKEND_CACHE is not None and now - _BACKEND_CACHE_TIME < 1.0:
        return dict(_BACKEND_CACHE)
    try:
        _backend()
        result = {'available': True, 'backend': BACKEND_NAME, 'version': BACKEND_VERSION, 'reason': ''}
    except (SkirtWiggleError, AttributeError, ReferenceError) as error:
        result = {'available': False, 'backend': BACKEND_NAME, 'version': BACKEND_VERSION, 'reason': str(error)}
    _BACKEND_CACHE, _BACKEND_CACHE_TIME = result, now
    return dict(result)


def _state(source):
    if source is None:
        return None
    raw = source.get(STATE_KEY)
    if raw is None:
        return None
    try:
        state = json.loads(raw)
        if (state.get('version') != 1 or state.get('mode') not in {'MANUAL', 'AUTOMATIC'}
                or not isinstance(state.get('owner'), str) or not isinstance(state.get('names'), list)
                or not isinstance(state.get('original', {}), dict) or not isinstance(state.get('lease', {}), dict)
                or not isinstance(state.get('settings', {}), dict)):
            raise ValueError()
        return state
    except (ValueError, TypeError, AttributeError) as error:
        raise SkirtWiggleError('Saved Dress motion settings need review; preserve this file.') from error


def mode(source):
    saved = _state(source)
    return saved['mode'] if saved else 'MANUAL'


def status(context=None, source=None):
    info = available()
    try:
        saved = _state(source)
        key = source.as_pointer() if source is not None else None
        running = key in _SESSIONS and not _SESSIONS[key].get('paused', False)
        selected = saved['mode'] if saved else 'MANUAL'
        reason = saved.get('reason', '') if saved else ''
        if running:
            message = 'Automatic uses Wiggle 2. Manual keeps the current skirt shape.'
        elif selected == 'AUTOMATIC':
            if _HISTORY_RECOVERY_PENDING:
                message = 'After Undo/Redo, choose Automatic or Manual again for this Dress. Saved Wiggle motion may still run until you choose.'
            else:
                message = reason or (info['reason'] if not info['available'] else 'Automatic is paused. Choose Automatic to resume.')
        else:
            message = reason or 'Manual keeps the current shape; pose the rings and handles.'
        return dict(info, mode=selected, active=key in _SESSIONS,
                    paused=bool(key in _SESSIONS and _SESSIONS[key].get('paused', False)),
                    running=running, message=message)
    except (ValueError, ReferenceError) as error:
        return dict(info, mode='MANUAL', running=False, message=str(error), reason=str(error))


def _write(source, record, selected, *, reason='', original=None, lease=None, settings=None):
    previous = _state(source) or {}
    state = {'version': 1, 'owner': record['owner'], 'mode': selected,
             'names': [name for chain in record['chains'] for name in chain['phys']],
             'backend': BACKEND_NAME, 'backend_version': BACKEND_VERSION,
             'reason': reason, 'original': previous.get('original', {}) if original is None else original,
             'lease': previous.get('lease', {}) if lease is None else lease,
             'settings': previous.get('settings', {}) if settings is None else settings}
    session = _SESSIONS.get(source.as_pointer())
    state['runtime_v2'] = (dict(version=1, component='SKIRT', source=source.name,
        rig=session['rig'].name, owner=record['owner'], names=list(session['names']),
        scene_token=source.get(STATE_SCENE_KEY),
        paused=bool(session['lease'].get('paused', False)),
        flags={name: {field: bool(getattr(session['bone_refs'][name], field)) for field in _FLAGS}
               for name in session['names']})
        if session and session.get('lease', {}).get('active') else None)
    source[STATE_KEY] = json.dumps(state, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _scene_token(scene):
    """A string identity keeps source export dependencies free of Scene IDs."""
    token = scene.get(SCENE_ID_KEY)
    try:
        valid = isinstance(token, str) and str(uuid.UUID(token)) == token
    except ValueError:
        valid = False
    if not valid or any(other != scene and other.get(SCENE_ID_KEY) == token for other in bpy.data.scenes):
        token = str(uuid.uuid4())
        scene[SCENE_ID_KEY] = token
    return token


def _origin_scene(source, rig):
    token = source.get(STATE_SCENE_KEY)
    if not isinstance(token, str):
        return None
    scenes = [scene for scene in rig.users_scene if scene.get(SCENE_ID_KEY) == token]
    return scenes[0] if len(scenes) == 1 else None


def _channels(bone):
    return {'mode': bone.rotation_mode, 'values': {key: tuple(getattr(bone, key)) for key in _CHANNELS}}


def _restore_channels(bone, saved):
    bone.rotation_mode = saved['mode']
    for name, value in saved['values'].items():
        setattr(bone, name, value)


def _raw(owner, name):
    exists = owner.is_property_set(name)
    value = getattr(owner, name) if exists else None
    if value is not None and type(value) not in (bool, int, float):
        _fail('Unsupported saved Wiggle setting: ' + name)
    return {'exists': exists, 'value': value}


@contextmanager
def _native_writes(backend):
    """Batch native RNA setters without the external selected-bone UI update.

    Wiggle 2's registered RNA lambdas resolve module.update_prop dynamically.
    Its normal callback propagates changes to every selected pose bone and
    rebuilds the whole scene. Own field setters need neither side effect.
    This synchronous main-thread scope performs setters only: no dependency
    updates, frame changes, operators, timers or solver callbacks run inside.
    """
    global _WRITE_DEPTH
    original = backend.update_prop
    backend.update_prop = lambda _owner, _context, _field: None
    _WRITE_DEPTH += 1
    try:
        yield
    finally:
        _WRITE_DEPTH -= 1
        backend.update_prop = original


def _native_set(owner, name, value):
    if not _WRITE_DEPTH:
        _fail('Native Dress settings require a scoped owned RNA write.')
    setattr(owner, name, value)


def _restore_raw(owner, name, saved):
    if not _WRITE_DEPTH:
        _fail('Native Dress restoration requires a scoped owned RNA write.')
    if owner.bl_rna.properties.get(name) is None:
        return  # The external add-on was disabled; do not create lookalike ID keys.
    if saved['exists']:
        setattr(owner, name, saved['value'])
    else:
        owner.property_unset(name)


def _pose(context, rig, record):
    evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
    names = [name for chain in record['chains'] for name in chain['def']]
    names.append(record['controls']['waist'])
    return {name: evaluated.pose.bones[name].matrix.copy() for name in names}


def _pose_same(context, rig, record, wanted):
    actual = _pose(context, rig, record)
    error = max(abs(a - b) for name in wanted for ar, br in zip(wanted[name], actual[name]) for a, b in zip(ar, br))
    if error > 5e-5:
        _fail(f'Dress mode could not keep the current pose ({error:.4g}); the switch was rolled back.')


def _paths(owner):
    from . import limb_ik
    data = owner.animation_data
    if data is None:
        return ()
    result = [curve.data_path for curve in data.drivers]
    actions = [data.action] if data.action else []
    def strips(items):
        for strip in items:
            if strip.action:
                actions.append(strip.action)
            if strip.type == 'META':
                strips(strip.strips)
    for track in data.nla_tracks:
        strips(track.strips)
    for action in set(actions):
        result.extend(curve.data_path for curve in limb_ik._fcurves_for_action(action))
    return tuple(result)


def _graph(source, record, rig, names):
    if record.get('physics'):
        _fail('This Dress uses a Cloth setup. Preserve it or remove that physics explicitly before choosing Wiggle.')
    saved = _state(source)
    if saved and (saved['owner'] != record['owner'] or set(saved['names']) != set(names)):
        _fail('The Dress motion ownership changed; restore its original generated bones.')
    holder, _id, influence_path = skirt.physics_control(source)
    value = holder.get('physics_influence', 0.0)
    if type(value) not in (int, float) or value not in (0.0, 1.0):
        _fail('Set the Dress Physics blend to 0 or 1 before choosing a motion mode.')
    prefixes = tuple(rig.pose.bones[name].path_from_id() for name in names)
    if any(path == influence_path or any(path.startswith(prefix + '.') or path.startswith(prefix + '[')
                                        for prefix in prefixes) or 'wiggle' in path for path in _paths(rig)):
        _fail('PHYS bones or the Physics switch have animation/drivers. Preserve those channels before choosing Wiggle.')
    for name in names:
        bone = rig.pose.bones[name]
        if bone.bone.use_deform or any(not item.mute for item in bone.constraints):
            _fail('A PHYS bone has an extra deformation role or constraint; preserve it before choosing Wiggle.')
    for chain in record['chains']:
        for name, physics in zip(chain['def'], chain['phys']):
            bone = rig.pose.bones[name]
            delta = bone.constraints.get('Skirt physics delta')
            manual = bone.constraints.get('Skirt manual pose')
            if (delta is None or delta.type != 'COPY_ROTATION' or delta.target != rig
                    or delta.subtarget != physics or delta.owner_space != 'LOCAL'
                    or delta.target_space != 'LOCAL' or delta.mix_mode != 'BEFORE' or delta.mute
                    or not all((delta.use_x, delta.use_y, delta.use_z))
                    or any((delta.invert_x, delta.invert_y, delta.invert_z)) or delta.euler_order != 'AUTO'
                    or manual is None or manual.type != 'COPY_TRANSFORMS' or manual.target != rig
                    or manual.subtarget != chain['manual'][chain['def'].index(name)]
                    or manual.owner_space != 'LOCAL' or manual.target_space != 'LOCAL'
                    or manual.mix_mode not in {'REPLACE', 'BEFORE_FULL'} or manual.mute
                    or abs(manual.influence - 1.0) > 1e-7
                    or list(bone.constraints).index(manual) > list(bone.constraints).index(delta)):
                _fail('The native Dress physics delta was edited; preserve that graph before choosing Wiggle.')
            path = delta.path_from_id('influence')
            curve = rig.animation_data.drivers.find(path) if rig.animation_data else None
            driver = curve.driver if curve else None
            if (curve is None or curve.mute or driver.type != 'AVERAGE' or len(driver.variables) != 1
                    or driver.variables[0].type != 'SINGLE_PROP' or len(driver.variables[0].targets) != 1
                    or driver.variables[0].targets[0].id != rig
                    or driver.variables[0].targets[0].data_path != influence_path):
                _fail('The Dress physics influence driver was changed; preserve it before choosing Wiggle.')
    return holder


def _preflight(context, source, backend):
    if context.scene != bpy.context.scene or context.mode not in {'OBJECT', 'POSE'}:
        _fail('Choose Dress motion in the active scene in Object or Pose Mode.')
    record = skirt.read_record(source)
    if record is None:
        _fail('Create the Dress controls before choosing Automatic.')
    rig = source[skirt.RIG_KEY]
    names = tuple(name for chain in record['chains'] for name in chain['phys'])
    from . import skirt_original_mode as original
    original_context = original.wiggle_original_context(source, record)
    if original_context is None:
        skirt._require_controls_for_setup(source)
    else:
        original.require_wiggle_original(context, source, record, names)
    from . import limb_ik
    if (source.library or source.override_library or not source.is_editable
            or rig.library or rig.override_library or not rig.is_editable
            or rig.data.library or (not limb_ik._single_armature_user(rig) and original_context is None)
            or rig.data.pose_position != 'POSE'
            or context.scene.objects.get(rig.name) != rig):
        _fail('Automatic needs this local Dress and a single-user Main Rig in the active scene.')
    holder = _graph(source, record, rig, names)
    scene = context.scene
    if scene.render.fps_base != 1.0 or scene.render.fps < 1:
        _fail('Wiggle 2 requires fps_base = 1 for this Dress motion mode.')
    if (not math.isfinite(scene.unit_settings.scale_length) or scene.unit_settings.scale_length <= 0
            or not math.isfinite(rig.matrix_world.determinant()) or abs(rig.matrix_world.determinant()) < 1e-10):
        _fail('Keep finite scene units and a nonzero Main Rig world scale before starting Automatic.')
    if any(path.startswith('wiggle') for path in _paths(scene)):
        _fail('Animated Scene Wiggle settings prevent a safe Dress mode switch.')
    if any(getattr(scene.wiggle, key) for key in ('is_rendering', 'is_preroll', 'reset')):
        _fail('Finish the current Wiggle render, reset or preroll operation first.')
    if rig.wiggle_freeze or rig.wiggle_mute:
        _fail('The Main Rig Wiggle settings are frozen or muted. Finish that operation before starting Dress motion.')
    from . import wiggle2_runtime
    owned = wiggle2_runtime.owned_bones(scene)
    for obj in scene.objects:
        if obj.type != 'ARMATURE':
            continue
        for bone in obj.pose.bones:
            if (bone.wiggle_head or bone.wiggle_tail) and (obj, bone.name) not in owned:
                _fail('Another Wiggle simulation is configured. Stop it before starting Automatic Dress motion.')
    for name in names:
        bone = rig.pose.bones[name]
        if any(getattr(bone, field) is not None for field in ('wiggle_collider', 'wiggle_collider_collection',
                                                             'wiggle_collider_head', 'wiggle_collider_collection_head',
                                                             'wiggle_wind_ob', 'wiggle_wind_ob_head')):
            _fail('A PHYS bone has a custom collider or wind source. Preserve that setup before choosing Dress motion.')
    return record, rig, names, holder


def _list_add(scene, rig, names):
    row = scene.wiggle.list.get(rig.name)
    if row is None:
        row = scene.wiggle.list.add()
        row.name = rig.name
    for name in names:
        if row.list.get(name) is None:
            item = row.list.add()
            item.name = name


def _list_remove(scene, rig_name, names):
    row = scene.wiggle.list.get(rig_name)
    if row is None:
        return
    for index in reversed(range(len(row.list))):
        if row.list[index].name in names:
            row.list.remove(index)
    if not row.list:
        for index, item in enumerate(scene.wiggle.list):
            if item == row:
                scene.wiggle.list.remove(index)
                break


def _start(context, source):
    from .animation_physics import require_enabled
    require_enabled(source, context=context, component='SKIRT')
    if _TEARDOWN_REASON is not None and _finish_deferred_stop() is not None:
        _fail('Finish the pending Dress motion cleanup in the active scene before starting Automatic.')
    try:
        backend = _backend()
    except (SkirtWiggleError, AttributeError, ReferenceError) as error:
        from .physics_dependencies import describe_failure
        _fail(describe_failure('SKIRT', error))
    from .physics_dependencies import note_verified
    note_verified('SKIRT', backend)
    if source.as_pointer() not in _SESSIONS:
        _recover_source(source)
    key = source.as_pointer()
    if key in _SESSIONS:
        if _SESSIONS[key].get('paused', False):
            set_paused(context, source, False)
        return 'Automatic Dress motion is already active.'
    record, rig, names, holder = _preflight(context, source, backend)
    from . import skirt_original_mode as original
    from . import wiggle2_runtime as runtime
    scene = context.scene
    saved = {'source': source, 'rig': rig, 'rig_name': rig.name, 'scene': scene, 'record': record,
             'names': names, 'backend': backend, 'frame': scene.frame_current,
             'raw': {name: {field: _raw(rig.pose.bones[name], field) for field in (*_FLAGS, *_DEFAULTS)} for name in names},
             'channels': {name: _channels(rig.pose.bones[name]) for name in names},
             'influence': holder.get('physics_influence'), 'holder': holder,
             'state_raw': source.get(STATE_KEY), 'scene_enable': _raw(scene, 'wiggle_enable'),
             'rig_enable': _raw(rig, 'wiggle_enable'), 'pose': _pose(context, rig, record),
             'bone_refs': {name: rig.pose.bones[name] for name in names}, 'scene_ref': source.get(STATE_SCENE_KEY),
             'scene_id_raw': scene.get(SCENE_ID_KEY), 'paused': False,
             'original_context': original.wiggle_original_context(source, record)}
    lease = runtime.acquire(context, 'SKIRT', source, rig, names, backend)
    saved['lease'] = lease
    saved['scene_enable'] = copy.deepcopy(lease['scene_original'])
    saved['rig_enable'] = copy.deepcopy(lease['rig_original'])
    _SESSIONS[key] = saved
    try:
        persisted_settings = (_state(source) or {}).get('settings', {})
        settings = {}
        for name in names:
            bone = rig.pose.bones[name]
            # The disabled PHYS layer's neutral rotation preserves influence0.
            if saved['influence'] == 0.0:
                bone.matrix_basis = Matrix.Identity(4)
            settings[name] = {}
            for field, value in _DEFAULTS.items():
                if not bone.is_property_set(field):
                    parameters = persisted_settings.get(name, {})
                    if not isinstance(parameters, dict):
                        _fail('Saved Dress Wiggle parameters are unreadable; preserve this file.')
                    candidate = parameters.get(field, value)
                    if type(candidate) not in (bool, int, float) or not math.isfinite(candidate):
                        _fail('Saved Dress Wiggle parameters are unreadable; preserve this file.')
                    settings[name][field] = candidate
        holder['physics_influence'] = 1.0
        runtime.configure(lease, settings)
        rig.update_tag(refresh={'OBJECT'})
        context.view_layer.update()
        _pose_same(context, rig, record, saved['pose'])
        if original.wiggle_original_context(source, record) != saved['original_context']:
            _fail('The Original skirt session changed while enabling physics; its inputs were restored.')
        source[STATE_SCENE_KEY] = _scene_token(scene)
        _write(source, record, 'AUTOMATIC', original=saved['raw'],
               lease={'scene_enable': saved['scene_enable'], 'rig_enable': saved['rig_enable']})
        register()
        if _frame_pre not in bpy.app.handlers.frame_change_pre:
            bpy.app.handlers.frame_change_pre.insert(0, _frame_pre)
        if not bpy.app.timers.is_registered(_timer):
            bpy.app.timers.register(_timer, first_interval=0.25)
        return 'Automatic Dress motion uses Wiggle 2. Play the timeline; Manual preserves the current shape.'
    except Exception as error:
        runtime.release(lease)
        _SESSIONS.pop(key, None)
        for name in names:
            _restore_channels(saved['bone_refs'][name], saved['channels'][name])
        holder['physics_influence'] = saved['influence']
        if saved['state_raw'] is None:
            source.pop(STATE_KEY, None)
        else:
            source[STATE_KEY] = saved['state_raw']
        if saved['scene_ref'] is None:
            source.pop(STATE_SCENE_KEY, None)
        else:
            source[STATE_SCENE_KEY] = saved['scene_ref']
        if saved['scene_id_raw'] is None:
            scene.pop(SCENE_ID_KEY, None)
        else:
            scene[SCENE_ID_KEY] = saved['scene_id_raw']
        context.view_layer.update()
        if isinstance(error, SkirtWiggleError):
            raise
        raise SkirtWiggleError('Automatic Dress motion failed and its inputs were restored: ' + str(error)) from error


def _stop(key, *, reason, preserve_preference=False):
    saved = _SESSIONS.get(key)
    if saved is None:
        return False
    source, rig, scene = saved['source'], saved['rig'], saved['scene']
    errors = []
    settings = {}
    for name in saved['names']:
        try:
            settings[name] = {field: getattr(saved['bone_refs'][name], field) for field in _DEFAULTS}
        except (ReferenceError, AttributeError):
            pass
    from . import wiggle2_runtime
    if saved.get('lease') is None:
        _fail('This Dress motion predates shared physics; preserve it and reconnect explicitly.')
    wiggle2_runtime.release(saved['lease'], restore_fields=_FLAGS)
    _SESSIONS.pop(key, None)
    try:
        # PHYS channels and influence remain exactly as currently evaluated.
        _write(source, saved['record'], 'AUTOMATIC' if preserve_preference else 'MANUAL', reason=reason, settings=settings)
        rig.update_tag(refresh={'OBJECT'})
    except ReferenceError:
        pass
    except (RuntimeError, TypeError, ValueError) as error:
        errors.append('saved mode: ' + str(error))
    if not _SESSIONS and not _IN_TIMER and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if not _SESSIONS:
        while _frame_pre in bpy.app.handlers.frame_change_pre:
            bpy.app.handlers.frame_change_pre.remove(_frame_pre)
    if errors:
        raise SkirtWiggleError('Dress motion stopped; its backend cleanup needs review: ' + '; '.join(errors))
    return True


def set_mode(context, source, selected):
    if selected not in {'AUTOMATIC', 'MANUAL'}:
        _fail('Choose Automatic or Manual Dress motion.')
    if selected == 'AUTOMATIC':
        return _start(context, source)
    key = source.as_pointer()
    if key in _SESSIONS:
        _stop(key, reason='Manual keeps the current skirt shape.')
    else:
        skirt._require_controls_for_setup(source)
        record = skirt.read_record(source)
        if record is None:
            _fail('Create the Dress controls first.')
        names = tuple(name for chain in record['chains'] for name in chain['phys'])
        _recover_source(source)
        _graph(source, record, source[skirt.RIG_KEY], names)
        _write(source, record, 'MANUAL', reason='Manual keeps the current skirt shape.')
    context.view_layer.update()
    return 'Manual keeps the current skirt shape. Use the rings and handles to refine it.'


def set_paused(context, source, paused):
    """Pause only this Dress's owned bones; retain its mode and peer motion."""
    saved = _SESSIONS.get(source.as_pointer())
    if (type(paused) is not bool or saved is None or saved['source'] != source
            or saved['scene'] != context.scene):
        _fail('The Dress physics session changed. Choose Automatic to reconnect it.')
    if _backend() is not saved['backend']:
        _fail('The Dress physics backend changed. Preserve the current preview before continuing.')
    reason = _changed(saved)
    if reason:
        _fail(reason)
    from . import wiggle2_runtime
    previous = saved.get('paused', False)
    state_raw = source.get(STATE_KEY)
    wiggle2_runtime.set_paused(saved['lease'], paused)
    try:
        saved['paused'] = paused
        _write(source, saved['record'], 'AUTOMATIC',
               reason='Dress physics is paused.' if paused else '')
    except Exception:
        wiggle2_runtime.set_paused(saved['lease'], previous)
        saved['paused'] = previous
        if state_raw is None:
            source.pop(STATE_KEY, None)
        else:
            source[STATE_KEY] = state_raw
        raise
    return dict(preview_state(source), mode='AUTOMATIC', available=True,
                backend=BACKEND_NAME, version=BACKEND_VERSION)


def suspend(context=None, source=None, *, reason='Automatic Dress motion paused; the current shape is kept.'):
    """Choose Manual for own active sources before Original/export/editing."""
    keys = [source.as_pointer()] if source is not None else list(_SESSIONS)
    count = 0
    for key in keys:
        count += bool(_stop(key, reason=reason))
    # A saved Automatic preference can already be paused because its backend
    # is unavailable. Original/export still explicitly choose Manual for it.
    scene = (context or bpy.context).scene
    candidates = (source,) if source is not None else tuple(scene.objects)
    for candidate in candidates:
        if candidate is None or candidate.type != 'MESH' or STATE_KEY not in candidate:
            continue
        _recover_source(candidate)
        if mode(candidate) == 'AUTOMATIC':
            record = skirt.read_record(candidate)
            if record is not None:
                _write(candidate, record, 'MANUAL', reason=reason)
                count += 1
    if context is not None:
        context.view_layer.update()
    return count


def suspend_for_rig(context, rig, *, reason='Original editing paused automatic Dress motion.'):
    """Pause only Dresses belonging to the selected character, including legacy rigs."""
    count = 0
    for source in tuple(context.scene.objects):
        if source.type != 'MESH' or STATE_KEY not in source:
            continue
        target = source.get(skirt.RIG_KEY)
        if target == rig or (target is not None and target.parent == rig):
            count += suspend(context, source, reason=reason)
    return count


def forget(context, source):
    """Stop this source's own motion, then discard only its new mode metadata.

    Removal may already have deleted the rig or bones. Cached live-session
    cleanup handles that case; a failed cleanup retains its recovery metadata.
    Existing-source setup rollback must restore metadata rather than call this.
    """
    key = source.as_pointer()
    if key in _SESSIONS:
        _stop(key, reason='Removing the Dress stopped its automatic motion.')
    elif isinstance(source.get(skirt.RIG_KEY), bpy.types.Object):
        _recover_source(source)
    source.pop(STATE_KEY, None)
    source.pop(STATE_SCENE_KEY, None)
    if context is not None:
        context.view_layer.update()


def preview_state(source):
    """Metadata/flat flags only: never hash files or dereference an idle PG."""
    session = _SESSIONS.get(source.as_pointer()) if source is not None else None
    if session:
        paused = bool(session.get('paused', False))
        return dict(active=True, paused=paused, running=not paused,
                    stopped_confirmed=paused, pending_recovery=False,
                    message='Dress physics is paused.' if paused else '')
    state = _state(source) if source is not None else None
    rig = source.get(skirt.RIG_KEY) if source is not None else None
    names = state.get('names', ()) if state else ()
    if not state and source is not None and source.get(skirt.RECORD_KEY):
        try:
            record = json.loads(source[skirt.RECORD_KEY])
            names = tuple(name for chain in record.get('chains', ()) for name in chain.get('phys', ()))
        except (ValueError, TypeError, AttributeError):
            _fail('Dress physics metadata needs review before its state can be confirmed.')
    native = bool(rig and any(bool(getattr(rig.pose.bones[name], field,
        rig.pose.bones[name].get(field, False))) for name in names if name in rig.pose.bones
        for field in ('wiggle_head', 'wiggle_tail')))
    return dict(active=False, paused=False, running=False,
                stopped_confirmed=not native, pending_recovery=native,
                message=('Saved Dress physics needs an explicit reconnect or stop.' if native else
                         'Automatic is paused. Choose Automatic to resume.' if state and state['mode'] == 'AUTOMATIC' else ''))


def prepare_recovery(context, source):
    """Prove a persisted produced state without performing any native write."""
    if source.as_pointer() in _SESSIONS:
        _fail('Use the active Dress physics controls rather than saved-state recovery.')
    info = preview_state(source)
    if info['stopped_confirmed']:
        return dict(source=source, changed=False, stopped_confirmed=True, reason='Dress physics is stopped.')
    state = _state(source)
    record = skirt.read_record(source)
    if record is None:
        _fail('Saved Dress physics has no complete generated ownership record.')
    names = tuple(name for chain in record['chains'] for name in chain['phys'])
    rig = source[skirt.RIG_KEY]
    if (context.scene != bpy.context.scene or context.scene.objects.get(source.name) != source
            or context.scene.objects.get(rig.name) != rig or context.mode not in {'OBJECT', 'POSE'}):
        _fail('Recover saved Dress physics in its active Scene in Object or Pose Mode.')
    if (not skirt.is_shared(record) and (rig.get(skirt.SOURCE_KEY) is not source
            or rig.data.get(skirt.OWNER_KEY) != record['owner'])):
        _fail('Saved Dress physics source and rig ownership no longer agree.')
    _graph(source, record, rig, names)
    marker = state.get('runtime_v2')
    origin = _origin_scene(source, rig)
    if (not isinstance(marker, dict) or marker.get('version') != 1 or marker.get('component') != 'SKIRT'
            or marker.get('source') != source.name or marker.get('rig') != rig.name
            or marker.get('owner') != record['owner'] or marker.get('names') != list(names)
            or state['owner'] != record['owner'] or set(state['names']) != set(names)
            or origin != context.scene or marker.get('scene_token') != source.get(STATE_SCENE_KEY)):
        _fail('Saved Dress physics lacks an exact shared-runtime ownership marker; preserve it before recovery.')
    original, flags = state.get('original'), marker.get('flags')
    if not isinstance(original, dict) or set(original) != set(names) or not isinstance(flags, dict) or set(flags) != set(names):
        _fail('Saved Dress physics recovery data is incomplete.')
    for name in names:
        if (not isinstance(original[name], dict) or set(original[name]) != set((*_FLAGS, *_DEFAULTS))
                or not isinstance(flags[name], dict) or set(flags[name]) != set(_FLAGS)
                or any(type(value) is not bool for value in flags[name].values())
                or flags[name] != dict(wiggle_enable=True, wiggle_head=False, wiggle_tail=True,
                    wiggle_mute=flags[name].get('wiggle_mute'), wiggle_head_mute=False, wiggle_tail_mute=False)):
            _fail('Saved Dress physics produced flags are not the supported state.')
        for field, saved in original[name].items():
            if (not isinstance(saved, dict) or type(saved.get('exists')) is not bool
                    or (saved['exists'] and (type(saved.get('value')) not in (bool, int, float)
                                             or not math.isfinite(saved['value'])))
                    or (field in {'wiggle_head', 'wiggle_tail'} and saved.get('value'))):
                _fail('Saved Dress physics original flags have foreign or unreadable values.')
        if any(getattr(rig.pose.bones[name], field) != value for field, value in flags[name].items()):
            _fail('Saved Dress physics flags changed externally; nothing was overwritten.')
    backend = _backend()  # Must prove live classes before any PG dereference.
    from . import wiggle2_runtime as runtime
    if any(runtime.owns(rig, name) for name in names):
        _fail('Another live shared-runtime owner already holds these Dress bones.')
    expected_members = {(rig.name, name) for name in names}
    members = runtime._members(origin)
    paused = marker.get('paused', False)
    if (type(paused) is not bool
            or (paused and (expected_members & members or not all(flags[name]['wiggle_mute'] for name in names)))
            or (not paused and not expected_members <= members)):
        _fail('Saved Dress physics list membership no longer matches its ownership marker.')
    lease = state.get('lease', {})
    for field in ('scene_enable', 'rig_enable'):
        saved = lease.get(field)
        if (not isinstance(saved, dict) or type(saved.get('exists')) is not bool
                or (saved['exists'] and type(saved.get('value')) is not bool)):
            _fail('Saved Dress physics enable recovery data is incomplete.')
    return dict(source=source, rig=rig, scene=origin, rig_name=rig.name, names=names,
        backend=backend, record=record, state_raw=source.get(STATE_KEY),
        before={name: {field: _raw(rig.pose.bones[name], field) for field in _FLAGS} for name in names},
        original=original, original_enable=lease,
        scene_before=_raw(origin, 'wiggle_enable'), rig_before=_raw(rig, 'wiggle_enable'),
        layout=[(row.name, tuple(item.name for item in row.list)) for row in origin.wiggle.list],
        changed=True, stopped_confirmed=False, reason='Saved Dress physics can be stopped safely.')


def recover_saved(context, source, token=None):
    """Explicitly stop only a fully proved persisted owner; never reconnect it."""
    fresh = prepare_recovery(context, source)
    if token is not None:
        if (token.get('source') != source or token.get('changed') != fresh['changed']
                or (fresh['changed'] and any(token.get(key) != fresh.get(key)
                    for key in ('state_raw', 'rig', 'scene', 'names', 'before')))):
            _fail('Saved Dress physics changed after its stop was prepared.')
    # Other proved owners may already have stopped in this same transaction.
    # Rollback must capture shared globals/list at the action's actual boundary.
    token = fresh
    if not token['changed']:
        return token
    rig, scene, names = token['rig'], token['scene'], token['names']
    from . import wiggle2_runtime as runtime
    try:
        with _native_writes(token['backend']):
            for name in names:
                for field in _FLAGS:
                    _restore_raw(rig.pose.bones[name], field, token['original'][name][field])
        runtime._list_remove(scene, rig.name, names)
        with _native_writes(token['backend']):
            if not any(b.wiggle_head or b.wiggle_tail for b in rig.pose.bones):
                _restore_raw(rig, 'wiggle_enable', token['original_enable']['rig_enable'])
            if not any(b.wiggle_head or b.wiggle_tail for ob in scene.objects if ob.type == 'ARMATURE' for b in ob.pose.bones):
                _restore_raw(scene, 'wiggle_enable', token['original_enable']['scene_enable'])
        _write(source, token['record'], 'AUTOMATIC', reason='Saved Dress physics is stopped. Choose Automatic to reconnect.')
        token['after'] = {name: {field: _raw(rig.pose.bones[name], field) for field in _FLAGS} for name in names}
        token['scene_after'], token['rig_after'] = _raw(scene, 'wiggle_enable'), _raw(rig, 'wiggle_enable')
        token['state_after'] = source.get(STATE_KEY)
        token['stopped_confirmed'] = True
        return token
    except Exception:
        _restore_recovery(token)
        raise


def _restore_recovery(token):
    from . import wiggle2_runtime as runtime
    rig, scene = token['rig'], token['scene']
    with _native_writes(token['backend']):
        for name in token['names']:
            for field in _FLAGS:
                _restore_raw(rig.pose.bones[name], field, token['before'][name][field])
        _restore_raw(scene, 'wiggle_enable', token['scene_before'])
        _restore_raw(rig, 'wiggle_enable', token['rig_before'])
    runtime._restore_members(token, dict(active=True,
        paused=not any(name == rig.name and set(items) & set(token['names'])
                       for name, items in token['layout']), layout=token['layout']))
    token['source'][STATE_KEY] = token['state_raw']
    token['stopped_confirmed'] = False


def rollback_recovery(context, token):
    if not token.get('changed'):
        return
    source, rig, scene = token['source'], token['rig'], token['scene']
    record = skirt.read_record(source)
    if (record is None or context.scene != scene or source.get(STATE_KEY) != token.get('state_after')
            or source.get(skirt.RIG_KEY) != rig or record['owner'] != token['record']['owner']):
        _fail('Recovered Dress ownership changed before rollback.')
    _graph(source, record, rig, token['names'])
    _backend()
    if (any(_raw(rig.pose.bones[name], field) != saved
            for name, fields in token['after'].items() for field, saved in fields.items())
            or _raw(scene, 'wiggle_enable') != token['scene_after']
            or _raw(rig, 'wiggle_enable') != token['rig_after']):
        _fail('Recovered Dress flags changed before rollback; external changes were retained.')
    _restore_recovery(token)


def _recover_source(source):
    return recover_saved(bpy.context, source)


def _changed(saved):
    rig, source, scene = saved['rig'], saved['source'], saved['scene']
    if scene != bpy.context.scene or rig.name != saved['rig_name'] or bpy.data.objects.get(source.name) != source:
        return 'Scene or Dress ownership changed; motion is paused.'
    if rig.mode not in {'OBJECT', 'POSE'} or rig.data.pose_position != 'POSE':
        return 'Editing the rig pauses automatic Dress motion.'
    if (not hasattr(bpy.types.PoseBone, 'wiggle_tail')
            or bpy.app.handlers.frame_change_pre.count(saved['backend'].wiggle_pre) != 1
            or bpy.app.handlers.frame_change_post.count(saved['backend'].wiggle_post) != 1):
        return 'The Wiggle backend was disabled; Dress motion is paused.'
    if (not saved.get('paused', False) and not scene.wiggle_enable) or rig.wiggle_mute or rig.wiggle_freeze:
        return 'Wiggle was muted or frozen; Dress motion is paused.'
    record = skirt.read_record(source)
    if record is None or record['owner'] != saved['record']['owner']:
        return 'Dress ownership changed; motion is paused.'
    from . import skirt_original_mode as original
    original_context = original.wiggle_original_context(source, record)
    has_original = (skirt._ORIGINAL_SESSION_KEY in rig
                    or isinstance(rig.get(skirt.ORIGINAL_DISPLAY_OWNER_KEY), bpy.types.Object)
                    and skirt._ORIGINAL_SESSION_KEY in rig[skirt.ORIGINAL_DISPLAY_OWNER_KEY])
    if has_original and (original_context is None or original_context != saved.get('original_context')):
        return 'The verified Original skirt session changed; its physics was paused.'
    holder = _graph(source, record, rig, saved['names'])
    if holder.get('physics_influence') != 1.0 or any(not rig.pose.bones[name].wiggle_tail for name in saved['names']):
        return 'The Dress physics switch was changed; automatic motion is paused.'
    from . import wiggle2_runtime
    if saved.get('lease') is None:
        return 'This Dress session predates shared physics. Reconnect it explicitly.'
    wiggle2_runtime.validate(saved['lease'])
    if bool(saved.get('paused', False)) != saved['lease']['paused']:
        return 'The Dress physics pause state changed outside these controls.'
    return ''


@persistent
def _frame_pre(scene, *args):
    for key, saved in list(_SESSIONS.items()):
        if saved['scene'] != scene:
            continue
        if saved.get('paused', False):
            saved['frame'] = scene.frame_current
            continue
        try:
            reason = _changed(saved)
            step = scene.frame_current - saved['frame']
            if reason:
                _stop(key, reason=reason)
            else:
                if step < 0 or step > 1:
                    # Timeline wraps and seeks are discontinuities, not elapsed
                    # physics time. Keep the current local pose and seed each
                    # endpoint there; skip the native reset/solve on this frame.
                    from . import wiggle2_runtime
                    bpy.context.view_layer.update()
                    wiggle2_runtime.reseed(saved['lease'], skip_frame=True)
                saved['frame'] = scene.frame_current
        except (ReferenceError, ValueError, RuntimeError) as error:
            try:
                _stop(key, reason='Dress motion paused: ' + str(error))
            except (ReferenceError, ValueError, RuntimeError):
                pass


def _timer():
    global _IN_TIMER
    _IN_TIMER = True
    try:
        for key, saved in list(_SESSIONS.items()):
            if saved.get('paused', False):
                continue
            try:
                reason = _changed(saved)
                if reason:
                    _stop(key, reason=reason)
            except (ReferenceError, ValueError, RuntimeError) as error:
                try:
                    _stop(key, reason='Dress motion paused: ' + str(error))
                except (ReferenceError, ValueError, RuntimeError):
                    pass
        return 0.25 if _SESSIONS else None
    finally:
        _IN_TIMER = False


@persistent
def _save_pre(*args):
    global _SAVE_RESUME
    _SAVE_RESUME = [saved['source'] for saved in _SESSIONS.values()]
    for key in list(_SESSIONS):
        _stop(key, reason='', preserve_preference=True)


@persistent
def _save_post(*args):
    global _SAVE_RESUME
    # Saving persists clean flags and the Automatic preference. Reconnecting
    # physics is an explicit action and never restarts a peer component.
    _SAVE_RESUME = []


@persistent
def _lifecycle_pre(*args):
    # Pre-load/render ends only live leases. Never recover an inactive source
    # or choose a new mode while a different native lifecycle is in progress.
    for key in list(_SESSIONS):
        _stop(key, reason='Scene editing paused automatic Dress motion; the current shape is kept.',
              preserve_preference=True)


@persistent
def _history_pre(*args):
    # The old IDs are still live before native history replacement. Release
    # only our solver lease and keep its preference and current PHYS channels;
    # an explicit reconnect reads the preference from the newly restored IDs.
    for key in list(_SESSIONS):
        _stop(key, reason='', preserve_preference=True)


def _resume_saved():
    # Retained callable identity for a previously queued timer. Shared physics
    # never reconnects from a timer or history/load callback.
    return None


def _queue_resume_saved(first_interval):
    """Own one pending callback without retaining any native scene reference."""
    if bpy.app.timers.is_registered(_resume_saved):
        bpy.app.timers.unregister(_resume_saved)
    if _REGISTERED:
        bpy.app.timers.register(_resume_saved, first_interval=first_interval)


@persistent
def _load_post(*args):
    global _BACKEND_CACHE, _HISTORY_RECOVERY_PENDING
    _BACKEND_CACHE = None
    _HISTORY_RECOVERY_PENDING = True
    from . import wiggle2_runtime
    wiggle2_runtime.forget('SKIRT')
    _SESSIONS.clear()
    if bpy.app.timers.is_registered(_resume_saved):
        bpy.app.timers.unregister(_resume_saved)


@persistent
def _undo_post(*args):
    global _SAVE_RESUME, _BACKEND_CACHE, _BACKEND_CACHE_TIME, _HISTORY_RECOVERY_PENDING
    # ed_undo_step_post can expose incompletely restored context RNA. Even
    # enumerating scene.objects here can crash natively before Python raises.
    # Drop stale Python references only. Neither this callback nor a timer may
    # recover the scene after history; the artist explicitly reconnects a Dress.
    _SESSIONS.clear()
    from . import wiggle2_runtime
    wiggle2_runtime.forget('SKIRT')
    _SAVE_RESUME = []
    _BACKEND_CACHE = None
    _BACKEND_CACHE_TIME = 0.0
    _HISTORY_RECOVERY_PENDING = True
    if bpy.app.timers.is_registered(_resume_saved):
        bpy.app.timers.unregister(_resume_saved)


def register():
    global _REGISTERED
    # The frame guard is installed only while our PHYS simulation is active.
    # An idle Dress adapter therefore does not add an unknown pose callback to
    # the separate Hair adapter's conservative callback ownership gate.
    for name, function in (('save_pre', _save_pre), ('save_post', _save_post),
                           ('load_pre', _lifecycle_pre), ('load_post', _load_post),
                           ('undo_pre', _history_pre), ('redo_pre', _history_pre),
                           ('undo_post', _undo_post), ('redo_post', _undo_post), ('render_pre', _lifecycle_pre)):
        collection = getattr(bpy.app.handlers, name)
        if function not in collection:
            collection.append(function)
    _REGISTERED = True
    if _TEARDOWN_REASON is not None:
        _schedule_deferred_stop(_TEARDOWN_REASON)


def _scene_context_available():
    return (hasattr(bpy.data, 'objects') and hasattr(bpy.data, 'scenes')
            and getattr(bpy.context, 'scene', None) is not None)


def _schedule_deferred_stop(reason):
    global _TEARDOWN_REASON
    _TEARDOWN_REASON = reason
    if not bpy.app.timers.is_registered(_finish_deferred_stop):
        bpy.app.timers.register(_finish_deferred_stop, first_interval=0.0)


def _finish_deferred_stop():
    """Restore owned native flags once RestrictBlend exposes live scene data."""
    global _TEARDOWN_REASON
    if _TEARDOWN_REASON is None:
        return None
    if not _scene_context_available():
        return 0.05
    for key, saved in list(_SESSIONS.items()):
        try:
            _stop(key, reason=_TEARDOWN_REASON)
        except Exception:
            # _stop removes its row first. Preserve that exact recovery record
            # if a native setter failed; do not lose it or retry forever.
            _SESSIONS.setdefault(key, saved)
            raise
    _TEARDOWN_REASON = None
    return None


def unregister():
    global _REGISTERED, _SAVE_RESUME, _HISTORY_RECOVERY_PENDING
    # Reload/unregister must not leave a history callback able to restart the
    # adapter after its classes and backend lifecycle have been removed.
    if bpy.app.timers.is_registered(_resume_saved):
        bpy.app.timers.unregister(_resume_saved)
    _HISTORY_RECOVERY_PENDING = False
    # Failed initialization may clean up under Blender's restricted context.
    # An adapter that never started owns no saved Automatic state to suspend.
    if _REGISTERED or _SESSIONS:
        reason = 'Character Designer stopped automatic Dress motion; the current shape is kept.'
        if _scene_context_available():
            suspend(reason=reason)
        elif _SESSIONS:
            # Keep native recovery references until the restricted registration
            # context exits. An idle adapter needs only callback cleanup.
            _schedule_deferred_stop(reason)
    for name, function in (('frame_change_pre', _frame_pre), ('save_pre', _save_pre), ('save_post', _save_post),
                           ('load_pre', _lifecycle_pre), ('load_post', _load_post), ('undo_pre', _history_pre),
                           ('redo_pre', _history_pre), ('undo_post', _undo_post), ('redo_post', _undo_post),
                           ('render_pre', _lifecycle_pre)):
        collection = getattr(bpy.app.handlers, name)
        while function in collection:
            collection.remove(function)
    for function in (_timer, _resume_saved):
        if bpy.app.timers.is_registered(function):
            bpy.app.timers.unregister(function)
    _SAVE_RESUME = []
    _REGISTERED = False
