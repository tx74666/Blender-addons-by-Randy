"""Explicitly frozen forearm playback on an isolated, disposable scene copy.

This is not an authoring validation cache. ``capture`` runs the complete
existing calculation on a private authoring source and issues a one-use frozen
template; ``prepare`` checks the disposable clone against it. Standalone valid
fixtures can prepare directly. The caller owns scene/control/action isolation
and suspension of authoring callbacks. No handlers or timers are registered.

Static edits to this playback copy (Rest, weights, topology, key coordinates,
profiles, constraints or drivers) require closing and rebuilding the binding.
``evaluate`` guards ID/count/name mappings; it does not claim to discover all
same-count direct RNA edits. Pose/world transforms, non-managed Shape Key
values and non-managed mute flags remain live, including evaluated drivers.

The author mesh and rig must be supplied as exclusion identities. Preparation,
evaluation, failure fallback and close write only private corrective keys.
"""

from __future__ import annotations

from array import array
from dataclasses import asdict, dataclass, replace
import hashlib
import math
from time import perf_counter
from weakref import WeakValueDictionary

import bpy
from mathutils import Matrix, Vector

from . import forearm_twist as authoring
from . import forearm_twist_profile as profile
from .forearm_twist_math import corrected_vertex, twist_angle


class PlaybackBindingError(ValueError):
    """The private playback binding must be rebuilt before it can publish."""


@dataclass(frozen=True)
class _ShapeInput:
    name: str
    pointer: int
    relative_pointer: int
    vertex_group: str
    deltas: tuple
    masks: tuple | None
    points: tuple | None
    relative_name: str


@dataclass(frozen=True)
class _Side:
    name: str
    key_name: str
    key_pointer: int
    lower: str
    hand: str
    axis: tuple
    pivot: tuple
    enabled: bool
    samples: tuple


@dataclass(frozen=True)
class ValidatedPlaybackTemplate:
    """Immutable, one-use result issued by :func:`capture` on a private source.

    A constructor call or copied dataclass is not an accepted proof. Only the
    exact instance registered by ``capture`` can be consumed by ``prepare``.
    The template never certifies an authoring scene in later runtime calls.
    """
    source_ids: tuple
    record_text: str
    counts: tuple
    topology: tuple
    group_mapping: tuple
    raw_weights: tuple
    bone_mapping: tuple
    pose_names: tuple
    rest_structure: tuple
    key_content: tuple
    basis_name: str
    indices: tuple
    basis_bytes: bytes
    basis_points: tuple
    weights: tuple
    bone_names: tuple
    rest_inverses: tuple
    rest_signature: tuple
    sides: tuple
    shapes: tuple
    snapshot_sha256: str
    authoring_validation_ms: float = 0.0
    capture_ms: float = 0.0

    @property
    def vertex_count(self):
        return self.counts[0]


_ISSUED_TEMPLATES = WeakValueDictionary()
_BIND_TOKEN = object()


@dataclass(frozen=True)
class Evaluation:
    """Separate measured stages; their sum is not displayed viewport FPS."""
    read_ms: float
    solve_ms: float
    publish_ms: float
    total_ms: float
    published: bool
    key_count: int
    corrected_vertex_count: int


def _require(condition, message):
    if not condition:
        raise PlaybackBindingError(message)


def _pointer(item):
    return int(item.as_pointer())


def _identity(item):
    return (getattr(item, 'session_uid', None), _pointer(item))


def _original(item):
    return getattr(item, 'original', item)


def _mesh_counts(mesh):
    return (len(mesh.vertices), len(mesh.edges), len(mesh.loops), len(mesh.polygons))


def _coordinates(items):
    values = array('f', [0.0]) * (3 * len(items))
    items.foreach_get('co', values)
    return values.tobytes()


def _purge_authoring_cache(pointer, name):
    """Discard only references created for this private mesh by preflight."""
    authoring._CACHE.pop(pointer, None)
    authoring._OUTPUT_CACHE.pop(pointer, None)
    for key in tuple(authoring._KEY_REFERENCES):
        if key[0] == pointer:
            authoring._KEY_REFERENCES.pop(key, None)
    authoring._ERRORS.pop(name, None)


def _assert_isolated(obj, armature, scene, source_mesh, source_rig, depsgraph):
    _require(source_mesh is not None and source_mesh.type == 'MESH',
             'Supply the author mesh exclusion identity.')
    _require(source_rig is not None and source_rig.type == 'ARMATURE',
             'Supply the author rig exclusion identity.')
    _require(obj.type == 'MESH' and armature.type == 'ARMATURE',
             'Playback needs a private mesh and armature.')
    _require(_pointer(obj) != _pointer(source_mesh)
             and _pointer(armature) != _pointer(source_rig)
             and _pointer(obj.data) != _pointer(source_mesh.data)
             and _pointer(armature.data) != _pointer(source_rig.data),
             'Playback must not use author objects or their data.')
    source_keys = source_mesh.data.shape_keys
    _require(source_keys is None or _pointer(obj.data.shape_keys) != _pointer(source_keys),
             'Playback Shape Keys must not share author data.')
    _require(obj.data.users == 1 and armature.data.users == 1,
             'Playback mesh and armature data must be single-user.')
    _require(obj.library is None and obj.data.library is None
             and armature.library is None and armature.data.library is None,
             'Playback copies must be local.')
    scene_pointer = _pointer(scene)
    _require({_pointer(item) for item in obj.users_scene} == {scene_pointer}
             and {_pointer(item) for item in armature.users_scene} == {scene_pointer},
             'Playback mesh and armature must belong only to the isolated scene.')
    _require(scene_pointer not in {_pointer(item) for item in source_mesh.users_scene}
             and scene_pointer not in {_pointer(item) for item in source_rig.users_scene},
             'Playback scene must be separate from the author scene.')
    _require(_pointer(_original(depsgraph.scene)) == scene_pointer,
             'Use the isolated scene dependency graph.')


def _profile_samples(record):
    """Compile the exact .41 sampler once, retaining saved support order."""
    knots = list(profile.profile_knots(record['rings']))
    bounded = 'range_start' in record
    if bounded:
        first, last = profile.record_range(record)
    else:
        knots = [(0.0, 0.0)] + [(p, r) for p, r in knots if 1.0e-6 < p < 1.0 - 1.0e-6] + [(1.0, 1.0)]
    continuous = record.get('distribution') == 'WRIST_CONTINUOUS'
    if continuous:
        # Canonical records use an explicit range for this distribution.
        _require(bounded, 'Continuous playback needs the canonical saved range.')
        sampler = profile.compile_sampler(record['rings'][first:last + 1])
    elif bounded:
        sampler = profile.compile_sampler(record['rings'], start=first, end=last,
                                          transition=record.get('transition', .1))
    else:
        sampler = profile.compile_sampler([{'position': p, 'ratio': r} for p, r in knots])
    result = []
    for index, position in zip(record['vertices'], record['positions']):
        ratio, influence = sampler(position)
        if continuous and position < record['rings'][first]['position']:
            influence = 0.0
        result.append((index, ratio, influence))
    _require(len(result) == len(record['vertices']) == len(record['positions']),
             'Playback support and position counts differ.')
    return tuple(result)


def _raw_weights(obj):
    return tuple(tuple((int(group.group), float(group.weight)) for group in vertex.groups)
                 for vertex in obj.data.vertices)


def _rest_structure(armature):
    return tuple((bone.name, bone.parent.name if bone.parent else None,
                  tuple(bone.head_local), tuple(bone.tail_local),
                  tuple(tuple(row) for row in bone.matrix_local),
                  bool(bone.use_connect), bool(bone.use_relative_parent), bone.inherit_scale)
                 for bone in armature.data.bones)


def _key_content(keys):
    return tuple((key.name, key.relative_key.name, key.vertex_group,
                  len(key.data), _coordinates(key.data)) for key in keys.key_blocks)


def _capture_inputs(obj, armature):
    """Copy source facts, retaining no native object in the frozen template."""
    keys = obj.data.shape_keys
    basis = keys.reference_key
    record_text = obj[authoring.RECORD_KEY]
    records = authoring._records(obj)
    indices = tuple(sorted({index for record in records.values() for index in record['vertices']}))
    weights = authoring._weights(obj, armature, indices)
    bone_names = tuple(sorted({name for values in weights.values() for name in values}
                              | {name for record in records.values() for name in record['chain']}))
    sides = []
    for side, record in records.items():
        key = authoring._managed_key(obj, side, record, repair_name=False)
        lower, hand = record['chain'][1:]
        bone = armature.data.bones[lower]
        sides.append(_Side(side, key.name, _pointer(key), lower, hand,
                           tuple((bone.tail_local - bone.head_local).normalized()),
                           tuple(armature.data.bones[hand].head_local),
                           bool(record.get('enabled', True)), _profile_samples(record)))
    owned_names = {record['key'] for record in records.values()}
    shapes = []
    for key in keys.key_blocks:
        if key == basis or key.name in owned_names:
            continue
        deltas = tuple(tuple(key.data[index].co - key.relative_key.data[index].co)
                       for index in indices)
        masks = None
        if key.vertex_group:
            mask = obj.vertex_groups.get(key.vertex_group)
            masks = tuple(next((float(group.weight) for group in obj.data.vertices[index].groups
                                if mask is not None and group.group == mask.index), 0.0)
                          for index in indices)
        points = (tuple(tuple(key.data[index].co) for index in indices)
                  if key.relative_key.name in owned_names else None)
        shapes.append(_ShapeInput(key.name, _pointer(key), _pointer(key.relative_key),
                                  key.vertex_group, deltas, masks, points, key.relative_key.name))
    values = {
        'source_ids': tuple(_identity(item) for item in (obj, obj.data, armature, armature.data, keys)),
        'record_text': record_text,
        'counts': _mesh_counts(obj.data),
        'topology': authoring.validation_cache.topology_token(obj.data),
        'group_mapping': tuple((group.index, group.name) for group in obj.vertex_groups),
        'raw_weights': _raw_weights(obj),
        'bone_mapping': tuple((bone.name, bool(bone.use_deform), int(bone.bbone_segments))
                              for bone in armature.data.bones),
        'pose_names': tuple(bone.name for bone in armature.pose.bones),
        'rest_structure': _rest_structure(armature),
        'key_content': _key_content(keys),
        'basis_name': basis.name,
        'indices': indices,
        'basis_bytes': _coordinates(basis.data),
        'basis_points': tuple(tuple(basis.data[index].co) for index in indices),
        'weights': tuple((index, tuple(weights[index].items())) for index in indices),
        'bone_names': bone_names,
        'rest_inverses': tuple((name, tuple(tuple(row) for row in
                                           armature.data.bones[name].matrix_local.inverted()))
                               for name in bone_names),
        'rest_signature': tuple((name, tuple(tuple(row) for row in
                                            armature.data.bones[name].matrix_local))
                                for name in bone_names),
        'sides': tuple(sides),
        'shapes': tuple(shapes),
    }
    digest = hashlib.sha256()
    # Avoid embedding native pointers in this content hash. It describes the
    # captured snapshot and is never accepted as a later authoring proof.
    for name in ('record_text', 'counts', 'topology', 'group_mapping', 'raw_weights',
                 'bone_mapping', 'pose_names', 'rest_structure', 'key_content'):
        digest.update(name.encode('ascii'))
        digest.update(repr(values[name]).encode('utf-8'))
    values['snapshot_sha256'] = digest.hexdigest()
    return ValidatedPlaybackTemplate(**values)


def _assert_target_content(obj, armature, template):
    """Validate an explicit cloned snapshot, without reusing Original ownership."""
    keys = obj.data.shape_keys
    _require(_mesh_counts(obj.data) == template.counts
             and authoring.validation_cache.topology_token(obj.data) == template.topology,
             'Playback clone topology differs from the captured snapshot.')
    _require(tuple((group.index, group.name) for group in obj.vertex_groups) == template.group_mapping
             and _raw_weights(obj) == template.raw_weights,
             'Playback clone vertex groups or raw weights differ.')
    _require(tuple((bone.name, bool(bone.use_deform), int(bone.bbone_segments))
                   for bone in armature.data.bones) == template.bone_mapping
             and tuple(bone.name for bone in armature.pose.bones) == template.pose_names
             and _rest_structure(armature) == template.rest_structure,
             'Playback clone bone mapping or Rest differs.')
    _require(keys is not None and keys.use_relative
             and keys.reference_key.name == template.basis_name
             and _key_content(keys) == template.key_content,
             'Playback clone Shape Key coordinates or relative/mask mapping differ.')
    normalized = authoring._weights(obj, armature, template.indices)
    _require(tuple((index, tuple(normalized[index].items())) for index in template.indices)
             == template.weights, 'Playback clone normalized weights differ.')


def capture(source_obj, source_depsgraph):
    """Run full authoring preflight, then issue a one-use immutable template.

    Use a PRIVATE authoring source in experiments. Canonical preflight can
    migrate records and publish corrective output exactly as normal authoring
    does; this API is not a read-only claim or an artist-scene transaction.
    Source authoring caches/handlers and their ordinary semantics stay intact.
    Root lifecycle code must protect an artist before invoking this there.
    """
    started = perf_counter()
    armature = authoring._check_mesh(source_obj)
    _require(source_obj.data.shape_keys is not None, 'Source mesh has no Shape Keys.')
    _require(source_obj.mode == 'OBJECT' and armature.mode != 'EDIT',
             'Capture outside mesh/bone editing.')
    _require(authoring.PREVIEW_KEY not in source_obj
             and not (authoring._SESSION and authoring._SESSION.get('mesh') == source_obj),
             'Finish source calibration before playback capture.')
    _require(bool(authoring._records(source_obj)), 'Source mesh has no forearm calibration.')
    scene_members = {_pointer(obj) for obj in _original(source_depsgraph.scene).objects}
    _require(_pointer(source_obj) in scene_members and _pointer(armature) in scene_members,
             'Capture using the source scene dependency graph.')
    authoring._calculate_object(source_obj, source_depsgraph)
    validation_done = perf_counter()
    template = _capture_inputs(source_obj, armature)
    finished = perf_counter()
    template = replace(template,
                       authoring_validation_ms=(validation_done - started) * 1000.0,
                       capture_ms=(finished - started) * 1000.0)
    _ISSUED_TEMPLATES[id(template)] = template
    return template


def discard_template(template):
    """Release an unused issuance after builder failure; contains no RNA refs."""
    if _ISSUED_TEMPLATES.get(id(template)) is template:
        _ISSUED_TEMPLATES.pop(id(template), None)


def check_template(template, *, source_mesh=None, source_rig=None):
    """Read-only issuance check for another explicit private conversion.

    This does not consume the template, verify later source content or act as
    a current authoring proof. A converter may copy immutable inputs only after
    this check, then must own its separate target validation and lifecycle.
    """
    _require(type(template) is ValidatedPlaybackTemplate
             and _ISSUED_TEMPLATES.get(id(template)) is template,
             'Supply the exact unused template issued by capture.')
    _require((source_mesh is None) == (source_rig is None),
             'Supply both source exclusion identities or neither.')
    if source_mesh is not None:
        current = tuple(_identity(item) for item in
                        (source_mesh, source_mesh.data, source_rig, source_rig.data,
                         source_mesh.data.shape_keys))
        _require(current == template.source_ids,
                 'Playback source exclusion identities differ from capture.')
    return template


def _consume_template(template, source_mesh, source_rig):
    issued = _ISSUED_TEMPLATES.pop(id(template), None)
    _require(type(template) is ValidatedPlaybackTemplate and issued is template,
             'Supply the exact unused template issued by capture.')
    current = tuple(_identity(item) for item in
                    (source_mesh, source_mesh.data, source_rig, source_rig.data,
                     source_mesh.data.shape_keys))
    _require(current == template.source_ids, 'Playback source exclusion identities differ from capture.')
    return template


class FrozenPlaybackBinding:
    """One private binding. Use :func:`prepare`; static edits require rebuild."""

    def __init__(self, obj, armature, scene, source_mesh, source_rig, depsgraph, inputs, *, _token=None):
        _require(_token is _BIND_TOKEN and type(inputs) is ValidatedPlaybackTemplate,
                 'Use prepare to create a validated playback binding.')
        self._closed = False
        self._failed = False
        self._last_error = None
        self._obj, self._armature, self._scene = obj, armature, scene
        self._keys = obj.data.shape_keys
        self._basis = self._keys.reference_key
        self._mesh = obj.data
        self._armature_data = armature.data
        self._ids = tuple(_identity(item) for item in
                          (obj, obj.data, armature, armature.data, self._keys, scene))
        self._scene_pointer = _pointer(scene)
        self._source_pointers = (_pointer(source_mesh), _pointer(source_mesh.data),
                                 _pointer(source_rig), _pointer(source_rig.data))
        self._object_names = (obj.name, obj.data.name, armature.name, armature.data.name,
                              self._keys.name, scene.name)
        self._counts = inputs.counts
        self._group_mapping = inputs.group_mapping
        self._bone_mapping = inputs.bone_mapping
        self._pose_names = inputs.pose_names
        self._key_mapping = tuple((key.name, _pointer(key), _pointer(key.relative_key),
                                   key.vertex_group, len(key.data))
                                  for key in self._keys.key_blocks)
        self._basis_pointer = _pointer(self._basis)
        # The clone can have no RECORD_KEY or an isolated marker. Its static
        # frame program comes from the admitted template, never old Original20
        # object names or serialized ownership identities on this target.
        self._record_text = obj.get(authoring.RECORD_KEY)
        self._indices = inputs.indices
        self._basis_bytes = inputs.basis_bytes
        self._basis_points = inputs.basis_points
        self._basis_by_index = dict(zip(self._indices, self._basis_points))
        self._weights = inputs.weights
        self._bone_names = inputs.bone_names
        self._rest_inverses = inputs.rest_inverses
        self._rest_signature = inputs.rest_signature
        self._sides = tuple(replace(side, key_pointer=_pointer(self._keys.key_blocks[side.key_name]))
                            for side in inputs.sides)
        self._managed = tuple(self._keys.key_blocks[side.key_name] for side in self._sides)
        self._shapes = tuple(replace(shape,
                                     pointer=_pointer(self._keys.key_blocks[shape.name]),
                                     relative_pointer=_pointer(self._keys.key_blocks[shape.relative_name]))
                             for shape in inputs.shapes)
        self._snapshot_sha256 = inputs.snapshot_sha256
        self.prepare_ms = 0.0
        self.authoring_validation_ms = 0.0
        self.compilation_ms = 0.0
        self.target_verification_ms = 0.0
        self.source_capture_ms = inputs.capture_ms
        self.preparation_route = None
        self.last_evaluation = None
        self._guard(depsgraph)

    def _guard(self, depsgraph):
        _require(not self._closed, 'Playback binding is closed.')
        _require(not self._failed, 'Playback binding failed; close and rebuild it.')
        obj, armature, keys = self._obj, self._armature, self._keys
        current_ids = tuple(_identity(item) for item in
                            (obj, obj.data, armature, armature.data, obj.data.shape_keys, self._scene))
        _require(current_ids == self._ids, 'Playback native binding identity changed.')
        _require((obj.name, obj.data.name, armature.name, armature.data.name,
                  keys.name, self._scene.name) == self._object_names,
                 'Playback native binding names changed.')
        _require(_pointer(obj) != self._source_pointers[0]
                 and _pointer(obj.data) != self._source_pointers[1]
                 and _pointer(armature) != self._source_pointers[2]
                 and _pointer(armature.data) != self._source_pointers[3]
                 and obj.data.users == 1 and armature.data.users == 1,
                 'Playback binding is no longer private.')
        _require({_pointer(item) for item in obj.users_scene} == {self._scene_pointer}
                 and {_pointer(item) for item in armature.users_scene} == {self._scene_pointer}
                 and _pointer(_original(depsgraph.scene)) == self._scene_pointer,
                 'Playback scene ownership or dependency graph changed.')
        _require(obj.mode == 'OBJECT' and armature.mode != 'EDIT',
                 'Leave editing and rebuild the playback binding.')
        _require(authoring._check_mesh(obj) == armature,
                 'Playback Armature modifier binding changed.')
        _require(_mesh_counts(obj.data) == self._counts
                 and tuple((group.index, group.name) for group in obj.vertex_groups) == self._group_mapping,
                 'Playback mesh count or vertex-group mapping changed.')
        _require(tuple((bone.name, bool(bone.use_deform), int(bone.bbone_segments))
                       for bone in armature.data.bones) == self._bone_mapping
                 and tuple(bone.name for bone in armature.pose.bones) == self._pose_names,
                 'Playback bone mapping changed.')
        _require(_pointer(keys.reference_key) == self._basis_pointer
                 and tuple((key.name, _pointer(key), _pointer(key.relative_key),
                            key.vertex_group, len(key.data)) for key in keys.key_blocks) == self._key_mapping,
                 'Playback Shape Key mapping changed.')
        _require(obj.get(authoring.RECORD_KEY) == self._record_text
                 and authoring.PREVIEW_KEY not in obj,
                 'Playback calibration changed; rebuild the binding.')

    def _read_frame(self, depsgraph):
        self._guard(depsgraph)
        base = {index: Vector(point) for index, point in zip(self._indices, self._basis_points)}
        evaluated_keys = self._keys.evaluated_get(depsgraph)
        for shape in self._shapes:
            key = self._keys.key_blocks.get(shape.name)
            _require(key is not None and _pointer(key) == shape.pointer,
                     'A playback Shape Key disappeared.')
            if key.mute:
                continue
            evaluated_key = evaluated_keys.key_blocks.get(shape.name)
            value = float(evaluated_key.value if evaluated_key is not None else key.value)
            _require(math.isfinite(value), 'A playback Shape Key value is non-finite.')
            if abs(value) < 1.0e-12:
                continue
            for ordinal, index in enumerate(self._indices):
                factor = value
                if shape.masks is not None:
                    factor *= shape.masks[ordinal]
                # Keep canonical Vector subtraction/multiply/add float32 order:
                # deltas were captured with that same native Vector subtraction.
                delta = (Vector(shape.deltas[ordinal]) if shape.points is None else
                         Vector(shape.points[ordinal]) - key.relative_key.data[index].co)
                base[index] += delta * factor
        eval_arm = self._armature.evaluated_get(depsgraph)
        eval_obj = self._obj.evaluated_get(depsgraph)
        to_arm = eval_arm.matrix_world.inverted() @ eval_obj.matrix_world
        from_arm = to_arm.inverted()
        transforms = {name: eval_arm.pose.bones[name].matrix @ Matrix(inverse)
                      for name, inverse in self._rest_inverses}
        return base, to_arm, from_arm, transforms

    def _solve_frame(self, base, to_arm, from_arm, transforms):
        weights = {index: dict(values) for index, values in self._weights}
        rows = {name: tuple(tuple(row) for row in matrix) for name, matrix in transforms.items()}
        blend_cache = {}
        outputs = []
        outside_pose_range = False
        corrected_count = 0
        for side, key in zip(self._sides, self._managed):
            coordinates = array('f')
            coordinates.frombytes(self._basis_bytes)
            if not side.enabled:
                outputs.append((key, False, coordinates))
                continue
            axis, pivot = Vector(side.axis), Vector(side.pivot)
            angle = twist_angle(transforms[side.lower], transforms[side.hand], axis)
            if abs(angle) > authoring.MAX_ANGLE + 1.0e-5:
                outside_pose_range = True
                continue
            for index, ratio, influence in side.samples:
                if influence == 0.0:
                    continue
                point = to_arm @ base[index]
                corrected = corrected_vertex(point, transforms, weights[index], side.lower,
                                             side.hand, axis, pivot, ratio, angle=angle,
                                             _matrix_rows=rows, _blend_cache=blend_cache)
                coordinate = Vector(self._basis_by_index[index]) + influence * (from_arm @ corrected - base[index])
                # RNA's per-vertex Vector assignment and array('f') both publish
                # float32 components. Native differential validation is required.
                offset = 3 * index
                coordinates[offset:offset + 3] = array('f', coordinate)
                corrected_count += 1
            outputs.append((key, side.enabled, coordinates))
        if outside_pose_range:
            raise authoring.ForearmTwistPoseRangeError(
                'Forearm twist is outside the prototype\'s -120 to +120 degree range.')
        return outputs, corrected_count

    def _mute_private_outputs(self):
        """Fail closed on the previously owned keys, never a new name match."""
        for side, key in zip(self._sides, self._managed):
            try:
                if (_pointer(key) == side.key_pointer
                        and _pointer(key.id_data) == self._ids[4][1]
                        and any(_pointer(item) == side.key_pointer for item in self._keys.key_blocks)):
                    key.mute = True
            except (ReferenceError, AttributeError, RuntimeError):
                continue

    def evaluate(self, depsgraph, *, publish=True):
        """Evaluate all sides before committing any coordinates on the copy.

        Any binding/math/publish error permanently invalidates this instance
        and mutes its private outputs; create a fresh binding to recover.
        ``publish=False`` computes the same outputs without writing Shape Keys.
        """
        started = perf_counter()
        try:
            frame_inputs = self._read_frame(depsgraph)
            read_done = perf_counter()
            outputs, corrected_count = self._solve_frame(*frame_inputs)
            solve_done = perf_counter()
            if publish:
                self._guard(depsgraph)
                # Calculation is transactional across both sides. If native
                # publishing itself fails, neither partial output remains active.
                for key, enabled, coordinates in outputs:
                    key.data.foreach_set('co', coordinates)
                    key.value = 1.0
                    key.mute = not enabled
                self._keys.update_tag()
                self._mesh.update()
                # Preserve evaluation of facial/expression drivers after Key
                # coordinate writes exactly as the authoring path does.
                self._keys.update_tag(refresh={'TIME'})
            finished = perf_counter()
            result = Evaluation((read_done - started) * 1000.0,
                                (solve_done - read_done) * 1000.0,
                                (finished - solve_done) * 1000.0,
                                (finished - started) * 1000.0,
                                bool(publish), len(outputs), corrected_count)
            self.last_evaluation = result
            return asdict(result)
        except Exception as exc:
            self._failed = True
            self._last_error = f'{type(exc).__name__}: {exc}'
            self._mute_private_outputs()
            raise

    def inspect(self):
        """Small ownership/contract metadata; no scene traversal or mutation."""
        return {
            'schema': 'character-designer.frozen-forearm-playback/1',
            'closed': self._closed,
            'failed': self._failed,
            'last_error': self._last_error,
            'binding_ids': self._ids,
            'excluded_source_pointers': self._source_pointers,
            'binding_names': self._object_names,
            'snapshot_sha256': self._snapshot_sha256,
            'static_input_policy': 'FROZEN_COPY_REBUILD_REQUIRED',
            'authoring_validation_ms': self.authoring_validation_ms,
            'compilation_ms': self.compilation_ms,
            'target_verification_ms': self.target_verification_ms,
            'source_capture_ms': self.source_capture_ms,
            'preparation_route': self.preparation_route,
            'prepare_ms': self.prepare_ms,
            'last_evaluation': (asdict(self.last_evaluation)
                                if self.last_evaluation is not None else None),
            'support_count': len(self._indices),
            'shape_input_count': len(self._shapes),
            'side_names': tuple(side.name for side in self._sides),
            'managed_key_names': tuple(side.key_name for side in self._sides),
            'automatic_handlers': False,
        }

    def close(self):
        """Idempotently mute owned private keys and release our native refs.

        The caller remains responsible for removing its isolated scene/assets.
        """
        if self._closed:
            return
        self._mute_private_outputs()
        _purge_authoring_cache(self._ids[0][1], self._object_names[0])
        self._closed = True
        self._obj = self._armature = self._scene = None
        self._mesh = self._armature_data = self._keys = self._basis = None
        self._managed = ()


def prepare(obj, depsgraph, *, isolated_scene, source_mesh, source_rig, template=None):
    """Validate and compile an already isolated complete control graph copy.

    The builder must clone/remap mesh, rig, keys, actions and every relevant
    control/driver reference before this call. This core verifies private mesh,
    rig, Key IDs and isolated scene membership; it does not certify that builder
    graph remapping. With an issued ``template``, exact static clone inputs are
    compared to that snapshot, and Original20 authoring validation is NOT run
    on the clone. Template consumption is one-use, including failed attempts.
    The clone's RECORD_KEY may be removed or replaced by an isolated marker.

    Without a template, this remains usable for standalone fixtures with valid
    private authoring identities. Canonical preflight may migrate only private
    calibration records and publish only private outputs in that route.
    """
    started = perf_counter()
    inputs = _consume_template(template, source_mesh, source_rig) if template is not None else None
    armature = authoring._check_mesh(obj)
    _require(obj.data.shape_keys is not None, 'Playback mesh has no Shape Keys.')
    _assert_isolated(obj, armature, isolated_scene, source_mesh, source_rig, depsgraph)
    _require(obj.mode == 'OBJECT' and armature.mode != 'EDIT',
             'Prepare private playback in Object Mode outside bone editing.')
    _require(authoring.PREVIEW_KEY not in obj
             and not (authoring._SESSION and authoring._SESSION.get('mesh') == obj),
             'Finish calibration before preparing playback.')
    pointer, name = _pointer(obj), obj.name
    try:
        if inputs is None:
            _require(bool(authoring._records(obj)), 'Playback mesh has no calibrated forearm records.')
            # Fixture route: errors propagate rather than update_runtime's catch.
            # This includes complete canonical ownership/Rest/topology checks.
            authoring._calculate_object(obj, depsgraph)
            validation_done = perf_counter()
            inputs = _capture_inputs(obj, armature)
        else:
            _assert_target_content(obj, armature, inputs)
            validation_done = perf_counter()
        binding = FrozenPlaybackBinding(obj, armature, isolated_scene, source_mesh, source_rig,
                                        depsgraph, inputs, _token=_BIND_TOKEN)
        finished = perf_counter()
        binding.authoring_validation_ms = ((validation_done - started) * 1000.0
                                          if template is None else inputs.authoring_validation_ms)
        binding.compilation_ms = (finished - validation_done) * 1000.0
        binding.target_verification_ms = ((validation_done - started) * 1000.0
                                          if template is not None else 0.0)
        binding.prepare_ms = (finished - started) * 1000.0
        binding.preparation_route = 'PRIVATE_FIXTURE' if template is None else 'CAPTURED_TEMPLATE'
        # The compiled path owns future outputs; authoring runtime cache entries
        # must not retain a stale proof or preserve this isolated scene afterward.
        _purge_authoring_cache(pointer, name)
        return binding
    except Exception:
        _purge_authoring_cache(pointer, name)
        raise
