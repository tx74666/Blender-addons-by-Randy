"""Experimental integer-frame helper TRS bake on an owned disposable rig.

No seeks, dependency-graph updates, handlers, operators, saves or registration.
The caller supplies the current graph after successful live helper verification;
sampling independently checks current source transforms, helper basis/evaluation
and the actual TRS channels against the same fixed matrix tolerance.
Only the already-copied active Action's selected channelbag receives curves.
The source Action, original bone channels and existing curves are never edited.
This prepares keys; it certifies neither geometry, viewport FPS nor subframes.
"""

from dataclasses import dataclass
import hashlib
import json
import math


class BoneBakeError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise BoneBakeError(message)


def _id(item):
    uid = getattr(item, 'session_uid', None)
    _require(type(uid) is int and uid > 0, 'Native ID session UID is required.')
    return uid, int(item.as_pointer())


def _plain(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if isinstance(value, float):
        _require(math.isfinite(value), 'Non-finite animation proof value.')
        return ('float', value.hex())
    if isinstance(value, (set, frozenset)):
        return ('set', tuple(sorted(_plain(item) for item in value)))
    try:
        return tuple(_plain(item) for item in value)
    except TypeError as exc:
        raise BoneBakeError('Unsupported animation proof value: ' + type(value).__name__) from exc


def _rna_values(item):
    return tuple((prop.identifier, _plain(getattr(item, prop.identifier)))
                 for prop in item.bl_rna.properties
                 if prop.identifier != 'rna_type'
                 and prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'})


def _properties(item):
    try:
        keys = tuple(item.keys())
    except AttributeError:
        return ('no-id-properties-api',)
    except TypeError as exc:
        if str(exc) != "bpy_struct.keys(): this type doesn't support IDProperties":
            raise
        return ('native-no-id-properties',)
    def value(input_value):
        if hasattr(input_value, 'as_pointer') and hasattr(input_value, 'session_uid'):
            return ('native-id', _id(input_value))
        if hasattr(input_value, 'keys'):
            return tuple((str(key), value(input_value[key])) for key in input_value.keys())
        return _plain(input_value)
    return tuple((str(key), value(item[key])) for key in keys)


def _curve_state(curve):
    driver = getattr(curve, 'driver', None)
    driver_state = None
    if driver is not None:
        driver_state = (_rna_values(driver), tuple(
            (_rna_values(variable), tuple((_rna_values(target), _id(target.id) if target.id else None)
                                          for target in variable.targets))
            for variable in driver.variables))
    group = getattr(curve, 'group', None)
    group_state = ((_rna_values(group), _properties(group),
                    _rna_values(group.colors) if hasattr(group, 'colors') else None) if group else None)
    return (_rna_values(curve), _properties(curve), group_state,
            tuple(_rna_values(point) for point in curve.keyframe_points),
            tuple(_rna_values(point) for point in curve.sampled_points),
            tuple((_rna_values(modifier), _properties(modifier),
                   tuple(_rna_values(point) for point in getattr(modifier, 'control_points', ())))
                  for modifier in curve.modifiers), driver_state)


def _bags(action):
    return tuple(bag for layer in action.layers for strip in layer.strips
                 for bag in strip.channelbags)


def _action_state(action, excluded=()):
    excluded = set(excluded)
    return (_id(action), _rna_values(action), tuple(_rna_values(slot) for slot in action.slots),
            tuple((_rna_values(layer), tuple((_rna_values(strip), tuple(
                (bag.slot_handle, tuple((int(curve.as_pointer()), _hash(_curve_state(curve)))
                                        for curve in bag.fcurves
                                        if int(curve.as_pointer()) not in excluded))
                for bag in strip.channelbags)) for strip in layer.strips)) for layer in action.layers))


def _old_bone_channels(rig, helpers):
    return tuple((pose.name, pose.rotation_mode,
                  _plain(tuple(getattr(pose, field) for field in
                               ('location', 'rotation_quaternion', 'rotation_euler',
                                'rotation_axis_angle', 'scale', 'matrix_basis'))))
                 for pose in rig.pose.bones if pose.name not in helpers)


def _animation_state(rig):
    ad = rig.animation_data
    return (_rna_values(ad), tuple(_hash(_curve_state(curve)) for curve in ad.drivers))


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class HelperSample:
    frame: int
    rows: tuple  # (helper name, location, quaternion, scale)


def _vector(values, width, label):
    values = tuple(values)
    _require(len(values) == width and all(isinstance(value, (int, float))
                                        and not isinstance(value, bool) and math.isfinite(value)
                                        for value in values), label + ' must contain finite components.')
    return tuple(float(value) for value in values)


def compile_channels(samples, helper_names):
    """Pure integer-domain compilation; it does not authenticate native input."""
    helper_names = tuple(helper_names)
    _require(helper_names and len(set(helper_names)) == len(helper_names), 'Helper names must be unique.')
    _require(all(isinstance(name, str) and name for name in helper_names), 'Invalid helper name.')
    samples = tuple(samples)
    _require(len(samples) == 30 and all(type(sample) is HelperSample for sample in samples),
             'Exactly 30 typed helper samples are required.')
    _require(tuple(sample.frame for sample in samples) == tuple(range(1, 31))
             and all(type(sample.frame) is int for sample in samples),
             'Samples must cover ordered integer frames 1 through 30.')
    channels, previous = {}, {}
    for sample in samples:
        _require(tuple(row[0] for row in sample.rows) == helper_names, 'Sample helper mapping changed.')
        for name, location, quaternion, scale in sample.rows:
            location = _vector(location, 3, 'Location')
            quaternion = _vector(quaternion, 4, 'Quaternion')
            scale = _vector(scale, 3, 'Scale')
            _require(sum(value * value for value in quaternion) > 1.0e-20, 'Quaternion is zero.')
            # Equivalent quaternion signs preserve integer-frame matrices and
            # avoid introducing a spurious component interpolation through zero.
            if name in previous and sum(a * b for a, b in zip(previous[name], quaternion)) < 0:
                quaternion = tuple(-value for value in quaternion)
            previous[name] = quaternion
            for field, values in (('location', location), ('rotation_quaternion', quaternion), ('scale', scale)):
                for component, value in enumerate(values):
                    channels.setdefault((name, field, component), []).append(value)
    return tuple((key, tuple(values)) for key, values in channels.items())


class HelperBakeSampler:
    """One append attempt to one explicitly owned private Action and slot."""
    def __init__(self, session, preview, *, source_rig, source_action):
        self.session, self.preview = session, preview
        self.source_rig, self.source_action = source_rig, source_action
        self.rig, self.scene = preview.rig, preview._scene
        self.action = self.rig.animation_data.action
        self.slot = self.rig.animation_data.action_slot
        self._ids = tuple(_id(item) for item in (self.rig, self.rig.data, self.scene, self.action))
        self._slot_handle = self.slot.handle
        self._helper_names = tuple(preview.helper_names[helper.key] for helper in preview.plan.helpers)
        self._samples, self._last_report = {}, None
        self.last_sampling_verification = None
        self._consumed = self._closed = False
        self._guard()
        self._bag = self._selected_bag()

    def _guard(self):
        import bpy
        from . import forearm_twist_bone_preview as backend
        _require(bpy.app.background, 'Helper bake is restricted to a disposable background worker.')
        _require(not self._closed and not self._consumed and not self.session.closed,
                 'Helper bake is closed or already attempted.')
        backend.check_built_preview(self.preview)
        self.preview._guard()
        _require(self.rig.get(backend.OWNER_KEY) == self.preview.token
                 and self.preview.mesh.get(backend.OWNER_KEY) == self.preview.token,
                 'Current private bone preview token changed.')
        _require(_id(self.session.scene) == _id(self.scene) and _id(self.preview.rig) == _id(self.rig),
                 'Private scene or preview owner changed.')
        mapping = self.session.id_map
        _require(mapping.get(self.source_rig) is not None and mapping.get(self.source_rig.data) is not None
                 and mapping.get(self.source_action) is not None
                 and _id(mapping[self.source_rig]) == _id(self.rig)
                 and _id(mapping[self.source_rig.data]) == _id(self.rig.data)
                 and _id(mapping[self.source_action]) == _id(self.action)
                 and _id(self.source_rig.animation_data.action) == _id(self.source_action),
                 'Rig/Action are not the declared source-excluded copies.')
        _require(_id(self.source_rig) != self._ids[0] and _id(self.source_action) != self._ids[3],
                 'An authoring ID cannot receive helper keys.')
        _require(tuple(_id(item) for item in (self.rig, self.rig.data, self.scene, self.action)) == self._ids,
                 'Private native IDs changed.')
        owned = {(pointer, uid) for _collection, pointer, uid in self.session.created}
        _require(all((pointer, uid) in owned for uid, pointer in self._ids),
                 'Private IDs are not in the session cleanup ownership manifest.')
        ad = self.rig.animation_data
        source_slot = self.source_rig.animation_data.action_slot
        _require(_id(ad.action) == _id(self.action) and ad.action_slot is not None and source_slot is not None
                 and ad.action_slot.handle == self._slot_handle
                 and ad.action_slot.identifier == source_slot.identifier
                 and self._slot_handle == source_slot.handle,
                 'The exact copied Action slot changed.')
        _require(not ad.use_nla and not ad.nla_tracks and not ad.use_tweak_mode
                 and ad.action_blend_type == 'REPLACE' and ad.action_influence == 1.0,
                 'Only an unblended active Action without NLA/tweak is supported.')
        _require(self.scene.frame_start == 1 and self.scene.frame_end == 30
                 and self.scene.render.fps == 30 and self.scene.render.fps_base == 1.0,
                 'This experiment supports only the admitted 1-30 frame, 30 FPS clip.')
        _require(self.action.library is None and self.rig.library is None,
                 'Writable private IDs must be local.')
        for helper in self.preview.plan.helpers:
            self.preview._helper(helper)

    def _selected_bag(self):
        _require(self.action.is_action_layered and len(self.action.layers) == 1
                 and len(self.action.layers[0].strips) == 1
                 and self.action.layers[0].strips[0].type == 'KEYFRAME',
                 'A single existing layered Keyframe Action is required.')
        bags = [bag for bag in self.action.layers[0].strips[0].channelbags
                if bag.slot_handle == self._slot_handle]
        _require(len(bags) == 1, 'The exact selected slot has no unique channelbag.')
        return bags[0]

    def _current_rows(self, frame, depsgraph):
        """Read and authenticate one current graph without seeking/updating it."""
        from mathutils import Matrix, Quaternion, Vector
        from . import forearm_twist_bone_preview as backend
        from .forearm_twist_math import TwistMathError, twist_angle
        scene = getattr(depsgraph.scene, 'original', depsgraph.scene)
        evaluated_scene = depsgraph.scene_eval
        _require(_id(scene) == _id(self.scene)
                 and _id(getattr(evaluated_scene, 'original', evaluated_scene)) == _id(self.scene),
                 'Sample with the explicit isolated scene dependency graph.')
        _require(evaluated_scene.frame_current == frame and evaluated_scene.frame_subframe == 0.0,
                 'The evaluated graph has not reached the requested integer frame.')
        evaluated_rig = self.rig.evaluated_get(depsgraph)
        _require(_id(getattr(evaluated_rig, 'original', evaluated_rig)) == _id(self.rig),
                 'The evaluated rig does not belong to this private sampler.')
        transforms, angles = {}, {}
        for side in self.preview.plan.sides:
            if not side.enabled:
                angles[side.name] = 0.0
                continue
            for name in (side.lower, side.hand):
                if name in transforms:
                    continue
                bone, pose = self.rig.data.bones.get(name), evaluated_rig.pose.bones.get(name)
                _require(bone is not None and pose is not None, 'A current source chain bone is missing.')
                transforms[name] = pose.matrix @ bone.matrix_local.inverted()
            try:
                angle = twist_angle(transforms[side.lower], transforms[side.hand], side.axis)
            except (TwistMathError, ValueError, TypeError) as error:
                raise BoneBakeError('Current sample deformation is unsupported: ' + str(error)) from error
            _require(abs(angle) <= backend.MAX_ANGLE + 1e-5, 'Current sample twist exceeds calibration.')
            angles[side.name] = angle
        desired_at_verification = self.preview._desired_deformations
        _require(desired_at_verification is not None
                 and set(desired_at_verification) == {helper.key for helper in self.preview.plan.helpers},
                 'The completed helper verification has no matching owned matrix snapshot.')
        rows, maximum = [], 0.0
        for helper in self.preview.plan.helpers:
            _bone, pose = self.preview._helper(helper)
            name = self.preview.helper_names[helper.key]
            evaluated_pose = evaluated_rig.pose.bones.get(name)
            _require(evaluated_pose is not None, 'A current evaluated helper is missing.')
            rest = self.preview._rest[helper.key]
            axis, pivot = Vector(helper.axis).normalized(), Vector(helper.pivot)
            rotation = (Matrix.Translation(pivot)
                        @ Quaternion(axis, helper.fraction * angles[helper.key[0]]).to_matrix().to_4x4()
                        @ Matrix.Translation(-pivot))
            desired = transforms[helper.source_bone] @ rotation
            basis = rest.inverted() @ desired @ rest
            location = _vector(pose.location, 3, 'Current helper location')
            quaternion = _vector(pose.rotation_quaternion, 4, 'Current helper quaternion')
            scale = _vector(pose.scale, 3, 'Current helper scale')
            _require(sum(value * value for value in quaternion) > 1.0e-20, 'Current helper quaternion is zero.')
            rebuilt = Matrix.LocRotScale(Vector(location), Quaternion(quaternion).normalized(), Vector(scale))
            # The retained verification is necessary but never a freshness
            # certificate. Recompute from this frame's actual source graph and
            # compare native basis, evaluated deformation and sampled channels.
            checks = ((desired_at_verification[helper.key], desired, 'Verified source pose became stale'),
                      (pose.matrix_basis, basis, 'Current helper basis changed'),
                      (evaluated_pose.matrix @ rest.inverted(), desired, 'Current evaluated helper became stale'),
                      (rebuilt, basis, 'Current sampled helper TRS differs from its required basis'))
            for actual, expected, label in checks:
                difference = backend.matrix_difference(actual, expected, tolerance=2e-6)
                _require(difference['within_tolerance'], label + ': ' + name)
                maximum = max(maximum, difference['max_abs'])
            rows.append((name, location, quaternion, scale))
        _require(tuple(row[0] for row in rows) == self._helper_names, 'Current helper sample mapping changed.')
        return tuple(rows), maximum

    def collect(self, frame, depsgraph):
        """Sample after verification, authenticating this frame's current graph."""
        self._guard()
        _require(type(frame) is int and 1 <= frame <= 30 and frame not in self._samples,
                 'Collect each integer frame 1-30 once.')
        _require(self.scene.frame_current == frame and self.scene.frame_subframe == 0.0,
                 'Caller must supply the matching integer frame without subframes.')
        report = self.preview.last_verification
        _require(report is not None and report is not self._last_report
                 and report.get('completed') and report.get('within_tolerance')
                 and not self.preview._verification_pending,
                 'A fresh completed live helper verification is required before sampling.')
        self.last_sampling_verification = {'completed': False, 'frame': frame, 'tolerance': 2e-6}
        rows, maximum = self._current_rows(frame, depsgraph)
        sample = HelperSample(frame, rows)
        self._samples[frame] = sample
        self._last_report = report
        self.last_sampling_verification.update(completed=True, max_abs=maximum, helper_count=len(rows))
        return sample

    def append(self):
        """Append helper curves once; caller owns geometry/timing verification."""
        self._guard()
        _require(int(self._selected_bag().as_pointer()) == int(self._bag.as_pointer()),
                 'Selected channelbag changed.')
        samples = tuple(self._samples[frame] for frame in sorted(self._samples))
        channels = compile_channels(samples, self._helper_names)
        paths = {key: self.rig.pose.bones[key[0]].path_from_id(key[1]) for key, _values in channels}
        helper_prefixes = tuple(self.rig.pose.bones[name].path_from_id() + '.' for name in self._helper_names)
        _require(not any(curve.data_path.startswith(helper_prefixes)
                         for bag in _bags(self.action) for curve in bag.fcurves),
                 'Existing helper animation curves collide with this bake.')
        _require(not any(curve.data_path.startswith(helper_prefixes) for curve in self.rig.animation_data.drivers),
                 'Existing helper drivers collide with this bake.')
        old_action = _action_state(self.action)
        old_channels = _old_bone_channels(self.rig, self._helper_names)
        old_animation = _animation_state(self.rig)
        source_state = (_action_state(self.source_action), _animation_state(self.source_rig),
                        _old_bone_channels(self.source_rig, ()))
        self._consumed = True
        created = []
        try:
            for key, values in channels:
                curve = self._bag.fcurves.new(data_path=paths[key], index=key[2])
                created.append(curve)
                curve.keyframe_points.add(30)
                curve.keyframe_points.foreach_set('co', [value for pair in zip(range(1, 31), values) for value in pair])
                for point in curve.keyframe_points:
                    point.interpolation = 'LINEAR'
                curve.update()
            _require(_action_state(self.action, (int(curve.as_pointer()) for curve in created)) == old_action,
                     'An existing Action curve, slot or layer changed while appending.')
            _require(_old_bone_channels(self.rig, self._helper_names) == old_channels
                     and _animation_state(self.rig) == old_animation,
                     'Original bone channels or animation settings/drivers changed.')
            _require((_action_state(self.source_action), _animation_state(self.source_rig),
                      _old_bone_channels(self.source_rig, ())) == source_state,
                     'Source authoring animation changed.')
        except Exception:
            for curve in reversed(created):
                self._bag.fcurves.remove(curve)
            raise
        return {'schema': 'character-designer.helper-bone-bake/1', 'completed': True,
                'frame_domain': [1, 30], 'integer_frames_only': True, 'sample_count': 30,
                'helper_count': len(self._helper_names), 'added_curves': len(created),
                'action_id': self._ids[3], 'slot_handle': self._slot_handle,
                'original_action_proof': _hash(old_action), 'original_bone_channels_proof': _hash(old_channels),
                'source_animation_proof': _hash(source_state), 'existing_animation_unchanged': True,
                'source_unchanged': True, 'native_geometry_verified': False,
                'gui_fps_verified': False, 'subframes_verified': False}

    def close(self):
        """Release sampler refs; session remains owner of appended private data."""
        self._closed = True
        self._samples.clear()
        self._last_report = None
        self.last_sampling_verification = None
        self.session = self.preview = self.rig = self.scene = self.action = self.slot = None
        self.source_rig = self.source_action = self._bag = None
