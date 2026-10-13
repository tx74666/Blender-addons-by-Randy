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
    for owner, typename in ((bpy.types.Scene, 'WiggleScene'), (bpy.types.Object, 'WiggleObject'),
                            (bpy.types.PoseBone, 'WiggleBone')):
        prop = owner.bl_rna.properties.get('wiggle')
        expected = getattr(module, typename, None)
        if (prop is None or prop.type != 'POINTER' or expected is None
                or prop.fixed_type.identifier != expected.bl_rna.identifier
                or not getattr(expected, 'is_registered', False)):
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
        running = key in _SESSIONS
        selected = saved['mode'] if saved else 'MANUAL'
        reason = saved.get('reason', '') if saved else ''
        if running:
            message = 'Automatic uses Wiggle 2. Manual keeps the current skirt shape.'
        elif selected == 'AUTOMATIC':
            message = reason or (info['reason'] if not info['available'] else 'Automatic is paused. Choose Automatic to resume.')
        else:
            message = reason or 'Manual keeps the current shape; pose the rings and handles.'
        return dict(info, mode=selected, running=running, message=message)
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
    skirt._require_controls_for_setup(source)
    record = skirt.read_record(source)
    if record is None:
        _fail('Create the Dress controls before choosing Automatic.')
    rig = source[skirt.RIG_KEY]
    if (source.library or source.override_library or not source.is_editable
            or rig.library or rig.override_library or not rig.is_editable
            or rig.data.library or rig.data.users != 1 or rig.data.pose_position != 'POSE'
            or context.scene.objects.get(rig.name) != rig):
        _fail('Automatic needs this local Dress and a single-user Main Rig in the active scene.')
    names = tuple(name for chain in record['chains'] for name in chain['phys'])
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
    from . import hair_wiggle_adapter
    if hair_wiggle_adapter.status(context).get('active'):
        _fail('Stop the active Hair preview before starting Automatic Dress motion.')
    owned = {(item['rig'], name) for item in _SESSIONS.values() for name in item['names']}
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
    backend = _backend()
    if source.as_pointer() not in _SESSIONS:
        _recover_source(source)
    record, rig, names, holder = _preflight(context, source, backend)
    key = source.as_pointer()
    if key in _SESSIONS:
        return 'Automatic Dress motion is already active.'
    scene = context.scene
    saved = {'source': source, 'rig': rig, 'rig_name': rig.name, 'scene': scene, 'record': record,
             'names': names, 'backend': backend, 'frame': scene.frame_current,
             'raw': {name: {field: _raw(rig.pose.bones[name], field) for field in (*_FLAGS, *_DEFAULTS)} for name in names},
             'channels': {name: _channels(rig.pose.bones[name]) for name in names},
             'influence': holder.get('physics_influence'), 'holder': holder,
             'state_raw': source.get(STATE_KEY), 'scene_enable': _raw(scene, 'wiggle_enable'),
             'rig_enable': _raw(rig, 'wiggle_enable'), 'pose': _pose(context, rig, record),
             'bone_refs': {name: rig.pose.bones[name] for name in names}, 'scene_ref': source.get(STATE_SCENE_KEY)}
    saved['scene_live'] = copy.deepcopy(saved['scene_enable'])
    saved['rig_live'] = copy.deepcopy(saved['rig_enable'])
    # All scene sessions share the first lease's original global enable value.
    other = next((item for item in _SESSIONS.values() if item['scene'] == scene), None)
    if other is not None:
        saved['scene_enable'] = copy.deepcopy(other['scene_enable'])
    same_rig = next((item for item in _SESSIONS.values() if item['rig'] == rig), None)
    if same_rig is not None:
        saved['rig_enable'] = copy.deepcopy(same_rig['rig_enable'])
    _SESSIONS[key] = saved
    try:
        persisted_settings = (_state(source) or {}).get('settings', {})
        with _native_writes(backend):
            for name in names:
                bone = rig.pose.bones[name]
                # A disabled PHYS layer's neutral rotation is the exact visual
                # result of influence 0. Do not reveal dormant solver rotations.
                if saved['influence'] == 0.0:
                    bone.matrix_basis = Matrix.Identity(4)
                for field, value in _DEFAULTS.items():
                    if not bone.is_property_set(field):
                        parameters = persisted_settings.get(name, {})
                        if not isinstance(parameters, dict):
                            _fail('Saved Dress Wiggle parameters are unreadable; preserve this file.')
                        candidate = parameters.get(field, value)
                        if type(candidate) not in (bool, int, float) or not math.isfinite(candidate):
                            _fail('Saved Dress Wiggle parameters are unreadable; preserve this file.')
                        _native_set(bone, field, candidate)
                for field, value in {'wiggle_enable': True, 'wiggle_head': False, 'wiggle_tail': True,
                                     'wiggle_mute': False, 'wiggle_head_mute': False, 'wiggle_tail_mute': False}.items():
                    _native_set(bone, field, value)
            _native_set(rig, 'wiggle_enable', True)
            _native_set(scene, 'wiggle_enable', True)
        holder['physics_influence'] = 1.0
        _list_add(scene, rig, names)
        rig.update_tag(refresh={'OBJECT'})
        context.view_layer.update()
        _pose_same(context, rig, record, saved['pose'])
        for name in names:
            backend.reset_bone(rig.pose.bones[name])
        scene.wiggle.lastframe = scene.frame_current
        _write(source, record, 'AUTOMATIC', original=saved['raw'],
               lease={'scene_enable': saved['scene_enable'], 'rig_enable': saved['rig_enable']})
        source[STATE_SCENE_KEY] = _scene_token(scene)
        register()
        if _frame_pre not in bpy.app.handlers.frame_change_pre:
            bpy.app.handlers.frame_change_pre.insert(0, _frame_pre)
        if not bpy.app.timers.is_registered(_timer):
            bpy.app.timers.register(_timer, first_interval=0.25)
        return 'Automatic Dress motion uses Wiggle 2. Play the timeline; Manual preserves the current shape.'
    except Exception as error:
        _SESSIONS.pop(key, None)
        _list_remove(scene, saved['rig_name'], names)
        with _native_writes(backend):
            for name in names:
                bone = rig.pose.bones.get(name)
                if bone is not None:
                    for field, value in saved['raw'][name].items():
                        _restore_raw(bone, field, value)
                    _restore_channels(bone, saved['channels'][name])
            _restore_raw(rig, 'wiggle_enable', saved['rig_live'])
            _restore_raw(scene, 'wiggle_enable', saved['scene_live'])
        holder['physics_influence'] = saved['influence']
        if saved['state_raw'] is None:
            source.pop(STATE_KEY, None)
        else:
            source[STATE_KEY] = saved['state_raw']
        if saved['scene_ref'] is None:
            source.pop(STATE_SCENE_KEY, None)
        else:
            source[STATE_SCENE_KEY] = saved['scene_ref']
        context.view_layer.update()
        if isinstance(error, SkirtWiggleError):
            raise
        raise SkirtWiggleError('Automatic Dress motion failed and its inputs were restored: ' + str(error)) from error


def _stop(key, *, reason, preserve_preference=False):
    saved = _SESSIONS.pop(key, None)
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
    with _native_writes(saved['backend']):
        for name in saved['names']:
            try:
                bone = saved['bone_refs'][name]
                # Cached references cannot modify a replacement same-name bone.
                for field in _FLAGS:
                    value = saved['raw'][name][field]
                    _restore_raw(bone, field, value)
            except ReferenceError:
                pass  # A deleted native bone has no remaining solver channels.
            except (RuntimeError, TypeError, ValueError) as error:
                errors.append(name + ': ' + str(error))
    try:
        if hasattr(bpy.types.Scene, 'wiggle'):
            _list_remove(scene, saved['rig_name'], saved['names'])
    except (ReferenceError, AttributeError):
        pass
    except (RuntimeError, TypeError, ValueError) as error:
        errors.append('native list: ' + str(error))
    for owner, field, original, kind in ((rig, 'wiggle_enable', saved['rig_enable'], 'rig'),
                                         (scene, 'wiggle_enable', saved['scene_enable'], 'scene')):
        try:
            armatures = (rig,) if kind == 'rig' else tuple(obj for obj in scene.objects if obj.type == 'ARMATURE')
            native_other = any((getattr(bone, 'wiggle_head', False) or getattr(bone, 'wiggle_tail', False))
                               for obj in armatures for bone in obj.pose.bones)
            if not native_other and not any(item[kind] == owner for item in _SESSIONS.values()):
                with _native_writes(saved['backend']):
                    _restore_raw(owner, field, original)
        except ReferenceError:
            pass
        except (RuntimeError, TypeError, ValueError) as error:
            errors.append(kind + ': ' + str(error))
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


def _recover_source(source):
    """Clear only saved owned native flags after undo/reload loses Python state.

    The saved PHYS pose is retained. Exact record ownership is rechecked before
    using persisted scalar snapshots; no solver or global rebuild is invoked.
    """
    if source.as_pointer() in _SESSIONS:
        return
    state = _state(source)
    if not state or not state.get('original'):
        return
    record = skirt.read_record(source)
    if record is None:
        return
    names = tuple(name for chain in record['chains'] for name in chain['phys'])
    if state['owner'] != record['owner'] or set(state['names']) != set(names):
        _fail('Saved Dress motion ownership changed; preserve this file before recovering it.')
    rig = source[skirt.RIG_KEY]
    origin = _origin_scene(source, rig)
    native = any(bool(getattr(rig.pose.bones[name], field, rig.pose.bones[name].get(field, False)))
                 for name in names for field in ('wiggle_head', 'wiggle_tail', 'wiggle_enable'))
    if hasattr(bpy.types.Scene, 'wiggle') and isinstance(origin, bpy.types.Scene):
        row = origin.wiggle.list.get(rig.name)
        native |= row is not None and any(item.name in names for item in row.list)
    if not native:
        return  # Clean Manual parameters, including new artist edits, are retained.
    original = state['original']
    if set(original) != set(names):
        _fail('Saved Dress native flags are incomplete; preserve this file.')
    # Validate the complete saved scalar topology before changing anything.
    for name in names:
        if not isinstance(original[name], dict):
            _fail('Saved Dress native property ownership is incomplete.')
        if set(original[name]) != set((*_FLAGS, *_DEFAULTS)):
            _fail('Saved Dress native property ownership is incomplete.')
        for field, saved in original[name].items():
            if (not isinstance(saved, dict) or type(saved.get('exists')) is not bool
                    or (saved['exists'] and (type(saved.get('value')) not in (bool, int, float)
                                             or not math.isfinite(saved['value'])))
                    or (field in {'wiggle_head', 'wiggle_tail'} and saved.get('value'))):
                _fail('Saved Dress native flags contain an unsupported pre-existing simulation.')
    lease = state.get('lease', {})
    for field in ('scene_enable', 'rig_enable'):
        saved = lease.get(field)
        if (not isinstance(saved, dict) or type(saved.get('exists')) is not bool
                or (saved['exists'] and type(saved.get('value')) is not bool)):
            _fail('Saved Dress global-enable ownership is incomplete; preserve this file.')
    backend = _backend()
    with _native_writes(backend):
        for name in names:
            for field in _FLAGS:
                _restore_raw(rig.pose.bones[name], field, original[name][field])
    if hasattr(bpy.types.Scene, 'wiggle'):
        # An ambiguous/deleted originating Scene never licenses restoring its
        # global switch elsewhere. Exact owned PHYS list rows can still be
        # removed from the rig's scenes so no orphan solver keeps running.
        for scene in (origin,) if origin is not None else rig.users_scene:
            _list_remove(scene, rig.name, names)
    other = any(getattr(bone, 'wiggle_head', False) or getattr(bone, 'wiggle_tail', False)
                for bone in rig.pose.bones)
    if not other and lease.get('rig_enable'):
        with _native_writes(backend):
            _restore_raw(rig, 'wiggle_enable', lease['rig_enable'])
    if origin is not None:
        active = any((getattr(bone, 'wiggle_head', False) or getattr(bone, 'wiggle_tail', False))
                     for obj in origin.objects if obj.type == 'ARMATURE' for bone in obj.pose.bones)
        if not active:
            with _native_writes(backend):
                _restore_raw(origin, 'wiggle_enable', lease['scene_enable'])


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
    if not scene.wiggle_enable or rig.wiggle_mute or rig.wiggle_freeze:
        return 'Wiggle was muted or frozen; Dress motion is paused.'
    if skirt._ORIGINAL_SESSION_KEY in rig:
        return 'Original editing paused automatic Dress motion.'
    record = skirt.read_record(source)
    if record is None or record['owner'] != saved['record']['owner']:
        return 'Dress ownership changed; motion is paused.'
    holder = _graph(source, record, rig, saved['names'])
    if holder.get('physics_influence') != 1.0 or any(not rig.pose.bones[name].wiggle_tail for name in saved['names']):
        return 'The Dress physics switch was changed; automatic motion is paused.'
    owned = {(item['rig'], name) for item in _SESSIONS.values() for name in item['names']}
    if any((bone.wiggle_head or bone.wiggle_tail) and (obj, bone.name) not in owned
           for obj in scene.objects if obj.type == 'ARMATURE' for bone in obj.pose.bones):
        return 'Another Wiggle simulation was enabled; Dress motion is paused and that simulation is retained.'
    return ''


@persistent
def _frame_pre(scene, *args):
    for key, saved in list(_SESSIONS.items()):
        if saved['scene'] != scene:
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
                    bpy.context.view_layer.update()
                    for name in saved['names']:
                        saved['backend'].reset_bone(saved['rig'].pose.bones[name])
                    scene.wiggle.lastframe = scene.frame_current
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
    sources, _SAVE_RESUME = _SAVE_RESUME, []
    for source in sources:
        try:
            if mode(source) == 'AUTOMATIC':
                _start(bpy.context, source)
        except (ReferenceError, ValueError, RuntimeError) as error:
            try:
                record = skirt.read_record(source)
                _write(source, record, 'AUTOMATIC', reason='Automatic is paused after saving: ' + str(error))
            except (ReferenceError, ValueError, RuntimeError):
                pass


@persistent
def _lifecycle_pre(*args):
    suspend(reason='Scene editing paused automatic Dress motion; the current shape is kept.')


def _resume_saved():
    for source in tuple(bpy.context.scene.objects):
        if source.type != 'MESH' or STATE_KEY not in source:
            continue
        try:
            _recover_source(source)
            if mode(source) != 'AUTOMATIC':
                continue
            _start(bpy.context, source)
        except (ReferenceError, ValueError, RuntimeError) as error:
            try:
                record = skirt.read_record(source)
                if record is not None:
                    _write(source, record, 'AUTOMATIC', reason='Saved Automatic is paused: ' + str(error))
            except (ReferenceError, ValueError, RuntimeError):
                pass
    return None


@persistent
def _load_post(*args):
    global _BACKEND_CACHE
    _BACKEND_CACHE = None
    if not bpy.app.timers.is_registered(_resume_saved):
        bpy.app.timers.register(_resume_saved, first_interval=0.1)


@persistent
def _undo_post(*args):
    # Blender restores ID data but not Python sessions. Recover immediately so
    # no unowned native solver step can run before the deferred safe reseed.
    for source in tuple(bpy.context.scene.objects):
        if source.type == 'MESH' and STATE_KEY in source:
            try:
                _recover_source(source)
            except (ReferenceError, ValueError, RuntimeError):
                pass
    _load_post()


def register():
    global _REGISTERED
    # The frame guard is installed only while our PHYS simulation is active.
    # An idle Dress adapter therefore does not add an unknown pose callback to
    # the separate Hair adapter's conservative callback ownership gate.
    for name, function in (('save_pre', _save_pre), ('save_post', _save_post),
                           ('load_pre', _lifecycle_pre), ('load_post', _load_post),
                           ('undo_pre', _lifecycle_pre), ('redo_pre', _lifecycle_pre),
                           ('undo_post', _undo_post), ('redo_post', _undo_post), ('render_pre', _lifecycle_pre)):
        collection = getattr(bpy.app.handlers, name)
        if function not in collection:
            collection.append(function)
    _REGISTERED = True


def unregister():
    global _REGISTERED, _SAVE_RESUME
    # Failed initialization may clean up under Blender's restricted context.
    # An adapter that never started owns no saved Automatic state to suspend.
    if _REGISTERED or _SESSIONS:
        suspend(reason='Character Designer stopped automatic Dress motion; the current shape is kept.')
    for name, function in (('frame_change_pre', _frame_pre), ('save_pre', _save_pre), ('save_post', _save_post),
                           ('load_pre', _lifecycle_pre), ('load_post', _load_post), ('undo_pre', _lifecycle_pre),
                           ('redo_pre', _lifecycle_pre), ('undo_post', _undo_post), ('redo_post', _undo_post),
                           ('render_pre', _lifecycle_pre)):
        collection = getattr(bpy.app.handlers, name)
        while function in collection:
            collection.remove(function)
    for function in (_timer, _resume_saved):
        if bpy.app.timers.is_registered(function):
            bpy.app.timers.unregister(function)
    _SAVE_RESUME = []
    _REGISTERED = False
