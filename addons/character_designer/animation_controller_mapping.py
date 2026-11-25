"""Prepare explicit same-name body motion for the current character controls.

This route has its own retargeting contract. The exact Unity bind-skeleton
importer retains its separate admission checks. A plan owns no Blender IDs and
does not assign Actions, seek scenes, or alter either rig.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math


SCHEMA = 'character-designer.controller-mapping/1'
RECORD = 'character_designer_controller_mapping_v1'
MAX_SAMPLES = 20001
MAX_KEYS = 500000
BODY_NAMES = (
    'Hips', 'spine', 'Chest', 'UpperChest', 'Neck', 'Head',
    *(part + '.' + side for side in ('L', 'R') for part in
      ('shoulder', 'upper_arm', 'forearm', 'hand', 'thigh', 'shin', 'foot', 'toe')),
)
FINGER_CHAINS = tuple(
    tuple(f'{finger}.{index:02d}.{side}' for index in range(1, 4))
    for side in ('L', 'R')
    for finger in ('thumb', 'f_index', 'f_middle', 'f_ring', 'f_pinky')
)


class MappingNeeded(ValueError):
    """The retained source needs a supported, verified controller mapping."""


@dataclass
class MappingPlan:
    source_rig: object
    source_action: object
    source_slot: int
    target_rig: object
    names: tuple
    source_times: tuple
    output_times: tuple
    source_world: object
    target_world: object
    source_rest_rotations: dict
    calibrated_target_rotations: dict
    source_rest_root: object
    target_rest_root: object
    root_motion_scale: float
    inventory: dict
    secondary_names: frozenset
    control_names: frozenset
    diagnostics: dict
    source_fingerprint: str
    target_fingerprint: str


@dataclass
class MappingResult:
    action: object
    slot: int
    source_fingerprint: str
    diagnostics: dict


def _require(condition, message):
    if not condition:
        raise MappingNeeded(message)


def _digest(value):
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _fps(value, label):
    _require(type(value) in (int, float) and math.isfinite(value) and value > 0,
             label + ' must be a positive finite frame rate.')
    return float(value)


def _scene_fps(scene):
    return _fps(scene.render.fps / scene.render.fps_base, 'Scene frame rate')


def _source_fps(source, explicit):
    if explicit is not None:
        return _fps(explicit, 'Source frame rate')
    rates = {_scene_fps(scene) for scene in source.users_scene}
    _require(len(rates) == 1, 'Choose the source editing scene frame rate for this motion.')
    return rates.pop()


def _names(source, target):
    required = set(BODY_NAMES)
    for label, rig in (('source', source), ('target', target)):
        missing = required - set(rig.data.bones.keys())
        _require(not missing, label.title() + ' body mapping is missing ' + ', '.join(sorted(missing)) + '.')
    fingers, eyes = set(), set()
    for chain in FINGER_CHAINS:
        present = set(chain) & (set(source.data.bones.keys()) | set(target.data.bones.keys()))
        if present:
            _require(all(name in rig.data.bones for rig in (source, target) for name in chain),
                     'Finger mapping needs the complete matching chain ' + ', '.join(chain) + '.')
            fingers.update(chain)
    for name in ('eye.L', 'eye.R'):
        if name in source.data.bones or name in target.data.bones:
            _require(name in source.data.bones and name in target.data.bones,
                     'Eye mapping needs the same native bone ' + name + ' in both rigs.')
            eyes.add(name)
    return required | fingers | eyes, fingers, eyes


def _native_parent(bone, controls):
    parent = bone.parent
    while parent is not None and parent.name in controls:
        parent = parent.parent
    return parent.name if parent is not None else None


def _check_hierarchy(source, target, names, controls):
    for name in names:
        source_parent = _native_parent(source.data.bones[name], ())
        target_parent = _native_parent(target.data.bones[name], controls)
        _require(source_parent == target_parent and source_parent in names | {None},
                 'Native parent mapping differs at ' + name + '.')
        for rig in (source, target):
            bone = rig.data.bones[name]
            _require(bone.bbone_segments == 1,
                     'B-Bone deformation needs its own mapping proof for ' + name + '.')
    _require(_native_parent(target.data.bones['Hips'], controls) is None,
             'Hips must be the mapped native body root.')


def _source_channels(source, action, slot, names):
    from . import animation_export_inputs as inputs
    from . import controller_animation as controller
    _require(action is not None and not action.asset_data and action.is_action_layered,
             'Choose a layered source motion Action with an explicit Object slot.')
    _require(type(slot) is int and slot != 0, 'Choose the explicit source Object Action slot.')
    curves = inputs._curves(action, slot)
    grouped = {(curve.data_path, curve.array_index): curve for curve in curves}
    _require(len(grouped) == len(curves), 'Duplicate source channels need a separate mapping.')
    selected = []
    for name in sorted(names):
        bone = source.pose.bones[name]
        rotation = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                    'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        for field in ('location', rotation, 'scale'):
            for index in range(len(getattr(bone, field))):
                curve = grouped.get((bone.path_from_id(field), index))
                _require(curve is not None and not curve.mute
                         and not getattr(getattr(curve, 'group', None), 'mute', False)
                         and len(curve.keyframe_points) > 0 and not curve.modifiers,
                         'Source mapping needs explicit unmodified keys for ' + name + '.' + field + '.')
                selected.append(curve)
    for curve in curves:
        _require(controller._bone_name(curve.data_path) is not None,
                 'Object or indirect source animation needs a separate mapping.')
    first, last = map(float, action.frame_range)
    _require(math.isfinite(first) and math.isfinite(last) and last > first,
             'Source mapping needs a finite nonempty clip range.')
    _require(last - first < MAX_SAMPLES, 'The source clip exceeds the mapping sample bound.')
    times = {first, last, *(float(frame) for frame in range(math.ceil(first), math.floor(last) + 1))}
    key_count = sum(len(curve.keyframe_points) for curve in selected)
    _require(key_count <= MAX_KEYS, 'The source body has too many keys for this mapping.')
    for curve in selected:
        for key in curve.keyframe_points:
            frame, value = map(float, key.co)
            _require(math.isfinite(frame) and math.isfinite(value), 'Source body keys must be finite.')
            if first <= frame <= last:
                times.add(frame)
    _require(len(times) <= MAX_SAMPLES, 'The source clip has too many mapping sample times.')
    return tuple(sorted(times))


def _current_world(rig):
    """Read supported object transforms without trusting a depsgraph cache."""
    from types import SimpleNamespace
    from . import animation_retarget as retarget
    _require(rig.parent is None and not rig.constraints,
             'Controller mapping needs an unparented Armature without object constraints.')
    # For these admitted objects matrix_basis is the authored world transform.
    # matrix_world can be a stale cache on a rig outside the active view layer.
    return retarget._uniform_world(SimpleNamespace(matrix_world=rig.matrix_basis.copy(), name=rig.name))


def _rest_fingerprint(rig):
    """Hash raw object/Rest inputs; unrelated helper hashes remain local."""
    return _digest({
        'world': [list(row) for row in rig.matrix_basis],
        'bones': {bone.name: {
            'parent': bone.parent.name if bone.parent else None,
            'matrix': [list(row) for row in bone.matrix_local],
            'tail': list(bone.tail_local), 'connected': bone.use_connect,
            'inherit_rotation': bone.use_inherit_rotation, 'inherit_scale': bone.inherit_scale,
            'rotation_mode': rig.pose.bones[bone.name].rotation_mode,
        } for bone in rig.data.bones},
    })


def _prepare(context, source, action, slot, target, source_fps, *, action_hash=None):
    from . import animation_retarget as retarget
    from . import controller_animation as controller
    from . import limb_ik
    for label, rig in (('source', source), ('target', target)):
        _require(rig is not None and rig.type == 'ARMATURE' and rig.mode != 'EDIT'
                 and rig.data.pose_position == 'POSE' and rig.parent is None and not rig.constraints,
                 'Choose an unparented ' + label + ' Armature in Pose Position outside Edit Mode.')
        _require(rig.data.animation_data is None, label.title() + ' Armature data is animated.')
    _require(source is not target and source.data is not target.data,
             'Source and target need independent Armatures for controller mapping.')
    _require(not target.library and not target.data.library and target.is_editable,
             'Choose a locally editable target character.')
    _require(not source.animation_data or not source.animation_data.drivers,
             'Source drivers need an explicitly baked source motion.')
    _require(not any(bone.constraints for bone in source.pose.bones),
             'Source constraints need an explicitly baked source motion.')
    controller._require_private_evaluation_idle()
    from . import natural_bind_graph
    if natural_bind_graph.KEY in target:
        binding = natural_bind_graph.validate(target)
        _require(not binding['attached'],
                 'This character uses a calibrated limb bind graph. Its private solver and keyed '
                 'offsets need an isolated animation mapping before a controller Custom can be created.')
    inventory = limb_ik._validate_inventory(target, limb_pose_only=True)
    _require(inventory['rigs'], 'Install the target character limb controls before mapping.')
    secondary = frozenset(controller._secondary_bones(target))
    controls = frozenset(controller._controls(target, inventory, pose_matching=True))
    names, fingers, eyes = _names(source, target)
    native, closure, _drivers = controller._closure(target, inventory, secondary, controls)
    additional = set(native) - names
    missing = additional - set(source.data.bones.keys())
    _require(not missing,
             'The current native body needs additional source mapping for ' + ', '.join(sorted(missing)) + '.')
    names.update(additional)
    _require(not names & (secondary | controls), 'A mapped native body bone belongs to generated controls.')
    _require(set(native) == names, 'The native body mapping overlaps an authenticated control or secondary bone.')
    _check_hierarchy(source, target, names, controls)
    source_times = _source_channels(source, action, slot, names)
    input_fps, output_fps = _source_fps(source, source_fps), _scene_fps(context.scene)
    first = source_times[0]
    output_times = tuple(1. + (frame - first) * output_fps / input_fps for frame in source_times)
    source_world, target_world = _current_world(source), _current_world(target)
    source_q, target_q = source_world.to_quaternion(), target_world.to_quaternion()
    rotations, angles = {}, {}
    landmarks = retarget._TARGET_LANDMARKS
    for name in names:
        source_direction = retarget._landmark_direction(source, name, landmarks.get(name), source_world)
        target_direction = retarget._landmark_direction(target, name, landmarks.get(name), target_world)
        swing = target_direction.rotation_difference(source_direction)
        rotations[name] = swing @ target_q @ target.data.bones[name].matrix_local.to_quaternion()
        angles[name] = math.degrees(swing.angle)
    root_scale = retarget._motion_scale(source, target, {name: name for name in names}, source_world, target_world)
    offset, api, _match, _limb, _inputs = controller._api()
    source_fingerprint = action_hash(action, slot) if action_hash else offset._action_hash(api, action, slot)
    target_fingerprint = _rest_fingerprint(target)
    ordered = tuple(sorted(names, key=lambda name: (len(target.data.bones[name].parent_recursive), name)))
    diagnostics = {
        'schema': SCHEMA, 'state': 'PREPARED',
        'source_action': action.name_full, 'source_slot': slot,
        'source_action_sha256': source_fingerprint,
        'source_rest_sha256': _rest_fingerprint(source), 'target_rest_sha256': target_fingerprint,
        'world_authority': 'raw matrix_basis of unparented, unconstrained inputs; matrix_world cache is diagnostic only',
        'source_world': [list(row) for row in source_world],
        'target_world': [list(row) for row in target_world],
        'source_cached_world': [list(row) for row in source.matrix_world],
        'target_cached_world': [list(row) for row in target.matrix_world],
        'body_bones': sorted(set(BODY_NAMES)), 'finger_bones': sorted(fingers), 'eye_bones': sorted(eyes),
        'additional_native_bones': sorted(additional),
        'excluded_source_bones': sorted(set(source.data.bones.keys()) - names),
        'source_only_excluded_bones': sorted(set(source.data.bones.keys()) - set(target.data.bones.keys())),
        'shared_excluded_source_bones': sorted((set(source.data.bones.keys()) & set(target.data.bones.keys())) - names),
        'retained_target_secondary_bones': sorted(secondary),
        'target_control_bones': sorted(controls), 'target_dependency_bones': sorted(closure),
        'source_fps': input_fps, 'target_fps': output_fps,
        'target_meters_per_unit': float(context.scene.unit_settings.scale_length),
        'source_range': [source_times[0], source_times[-1]],
        'output_range': [output_times[0], output_times[-1]],
        'sample_count': len(source_times), 'root_motion_scale': root_scale,
        'calibration_angles_degrees': angles,
        'mapping': 'same native names and ancestry; anatomical rest-delta rotation',
        'joint_translation_policy': 'connected target joints retain target Rest connections; unconnected joints retain mapped source translations',
        'projected_connected_translations': {},
        'root_reference': 'target Rest Hips; source displacement measured from source Rest Hips',
        'coverage': 'native body, complete matching fingers and eyes; all other source bone animation excluded',
        'native_output_verified': False,
    }
    return MappingPlan(source, action, slot, target, ordered, source_times, output_times,
                       source_world, target_world,
                       {name: source_q @ source.data.bones[name].matrix_local.to_quaternion() for name in names},
                       rotations, source_world @ source.data.bones['Hips'].head_local,
                       target_world @ target.data.bones['Hips'].head_local, root_scale,
                       inventory, secondary, controls, diagnostics, source_fingerprint, target_fingerprint)


def prepare(context, source_rig, source_action, source_slot, target_rig, *, source_fps=None, action_hash=None):
    """Validate read-only inputs and describe a bounded mapping candidate.

    Source fractional key times and whole-frame samples retain their original
    duration at the target scene frame rate. Preparation never proves native
    playback or publishes a Custom Action.
    """
    try:
        return _prepare(context, source_rig, source_action, source_slot, target_rig, source_fps,
                        action_hash=action_hash)
    except MappingNeeded:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError) as error:
        raise MappingNeeded('Controller mapping needs supported inputs: ' + str(error)) from error


def _body_control_plan(plan):
    """Authenticate the body closure without importing secondary animation."""
    from . import controller_animation as controller, limb_ik_fk
    from . import root_control, torso_controls, spine_ik_fk, eye_controls, foot_controls
    from .unity_animation_controls import ControlPlan
    rig, inventory = plan.target_rig, plan.inventory
    _native, closure, active_drivers = controller._closure(
        rig, inventory, plan.secondary_names, plan.control_names)
    accepted, drivers = set(), set()
    redirects, switches, neutral, eyes = {}, {}, set(), {}
    for bone, constraint, record in inventory['records']:
        accepted.add((bone.name, constraint.name))
        if limb_ik_fk.validate_constraint_influence(rig, bone, constraint, record):
            drivers.add((constraint.path_from_id('influence'), 0))
    for entry in inventory['rigs'].values():
        switches[(entry['target'].name, limb_ik_fk.PROPERTY)] = 0.
    if inventory.get('master'):
        neutral.add(inventory['master'].name)
    root = root_control.validate(rig, inventory, pose_only=True)
    torso = torso_controls.validate(rig, inventory, pose_only=True)
    spine = spine_ik_fk.validate(rig, inventory, pose_only=True)
    eye = eye_controls.validate(rig, inventory, pose_only=True)
    feet = foot_controls.validate(rig, inventory, limb_pose_only=True)
    for record in (root, torso, spine, eye, *feet.values()):
        if record:
            accepted.update((row['owner'], row['name']) for row in record['constraints'])
    if root:
        neutral.add(root['master'])
        switches[(root['master'], root_control.SCALE_PROPERTY)] = 1.
        path = rig.pose.bones[root['master']].path_from_id('scale')
        drivers.update((path, index) for index in range(3))
    if torso:
        redirects.update(torso['controls'])
        neutral.add(torso['bend'])
    if spine:
        redirects.update(spine['fk_controls'])
        switches[(spine['chest'], spine_ik_fk.PROPERTY)] = 0.
        drivers.update((row['path'], row['index']) for row in spine['drivers'])
    for record in feet.values():
        redirects[record['toe']] = record['toe_control']
        neutral.add(record['roll'])
        drivers.update((row['path'], row['index']) for row in record['drivers'])
    if eye:
        neutral.add(eye['master'])
        eyes.update((name, (eye['targets'][side], float(eye['distance'])))
                    for name, side in zip(eye['sources'], ('L', 'R')))
    for name in closure:
        _require(all((name, constraint.name) in accepted for constraint in rig.pose.bones[name].constraints),
                 'An additional body constraint needs a controller mapping for ' + name + '.')
    _require(active_drivers <= drivers,
             'Additional body drivers need an explicit controller mapping.')
    return ControlPlan(frozenset(plan.names), plan.control_names, frozenset(accepted),
                       frozenset(drivers), redirects, switches, frozenset(neutral), eyes)


def _neutral_target(context, target, control_plan):
    from mathutils import Matrix
    for bone in target.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
    for (name, prop), value in control_plan.switches.items():
        target.pose.bones[name][prop] = value
    context.view_layer.update()


def _source_copy(scene, plan):
    """Caller owns both returned IDs even if later source evaluation fails."""
    from mathutils import Matrix
    import bpy
    clone, data = None, None
    try:
        clone = plan.source_rig.copy()
        data = plan.source_rig.data.copy()
        clone.data = data
        data.use_fake_user = False
        scene.collection.objects.link(clone)
        clone.hide_viewport = False
        clone.hide_render = True
        clone.hide_set(False, view_layer=scene.view_layers[0])
        for bone in clone.pose.bones:
            bone.matrix_basis = Matrix.Identity(4)
        animation = clone.animation_data_create()
        animation.action = plan.source_action
        animation.action_slot = next(slot for slot in plan.source_action.slots if slot.handle == plan.source_slot)
        animation.use_nla = False
        animation.action_blend_type, animation.action_influence = 'REPLACE', 1.
        settings = getattr(clone, 'wiggle', None)
        if settings is not None:
            settings.freeze = True
        return clone, data
    except Exception:
        if clone is not None:
            bpy.data.objects.remove(clone, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.armatures.remove(data)
        raise


def _desired(context, source, plan, source_frame):
    """Evaluate the original source independently at each requested time."""
    from mathutils import Matrix
    from . import controller_animation as controller
    controller._frame(context.scene, source_frame)
    context.view_layer.update()
    evaluated = source.evaluated_get(context.evaluated_depsgraph_get())
    world_q = plan.source_world.to_quaternion()
    inverse_target_q = plan.target_world.to_quaternion().inverted()
    desired = {}
    for name in plan.names:
        source_matrix = evaluated.pose.bones[name].matrix
        location, rotation, scale = source_matrix.decompose()
        rebuilt = Matrix.LocRotScale(location, rotation, scale)
        _require(max(abs(source_matrix[i][j] - rebuilt[i][j]) for i in range(4) for j in range(4)) <= 2.e-5,
                 'Source bone shear needs a separate mapping for ' + name + '.')
        delta = world_q @ rotation @ plan.source_rest_rotations[name].inverted()
        mapped_rotation = inverse_target_q @ delta @ plan.calibrated_target_rotations[name]
        bone = plan.target_rig.data.bones[name]
        parent = _native_parent(bone, plan.control_names)
        if parent is None:
            source_position = plan.source_world @ location
            position = plan.target_world.inverted() @ (
                plan.target_rest_root + (source_position - plan.source_rest_root) * plan.root_motion_scale)
        else:
            source_parent = plan.source_rig.data.bones[parent]
            target_parent = plan.target_rig.data.bones[parent]
            source_parent_pose = evaluated.pose.bones[parent].matrix
            rest_offset = source_parent.matrix_local.inverted() @ plan.source_rig.data.bones[name].head_local
            pose_offset = source_parent_pose.inverted() @ location
            extra = pose_offset - rest_offset
            _require(source_parent.length > 1.e-8 and target_parent.length > 1.e-8,
                     'A zero-length native parent needs a separate mapping for ' + name + '.')
            mapped_extra = (plan.calibrated_target_rotations[parent].inverted()
                            @ plan.source_rest_rotations[parent] @ extra)
            mapped_extra *= target_parent.length / source_parent.length
            target_offset = target_parent.matrix_local.inverted() @ bone.head_local
            if bone.use_connect:
                neutral = bone.convert_local_to_pose(Matrix.Identity(4), bone.matrix_local,
                    parent_matrix=desired[parent], parent_matrix_local=target_parent.matrix_local)
                position = neutral.translation
                omitted = plan.target_world.to_3x3() @ desired[parent].to_3x3() @ mapped_extra
                measurements = plan.diagnostics['projected_connected_translations'].setdefault(name, {
                    'maximum_source_parent_offset': 0., 'maximum_mapped_world_offset_meters': 0.})
                measurements['maximum_source_parent_offset'] = max(
                    measurements['maximum_source_parent_offset'], extra.length)
                measurements['maximum_mapped_world_offset_meters'] = max(
                    measurements['maximum_mapped_world_offset_meters'],
                    omitted.length * plan.diagnostics['target_meters_per_unit'])
            else:
                position = desired[parent] @ (target_offset + mapped_extra)
        desired[name] = Matrix.LocRotScale(position, mapped_rotation, scale)
    return desired


def _identity_curve(bone, curve):
    """Prove an entire local channel constant, including Bezier handles."""
    path = curve.data_path
    value = 0.
    if path == bone.path_from_id('scale') or (path == bone.path_from_id('rotation_quaternion')
                                              and curve.array_index == 0):
        value = 1.
    elif path == bone.path_from_id('rotation_axis_angle') and curve.array_index != 0:
        # A zero angle is sufficient, regardless of the axis.
        return True
    if curve.modifiers or curve.sampled_points:
        return False
    return all(point.interpolation in {'CONSTANT', 'LINEAR', 'BEZIER'} and point.co.y == value
               and point.handle_left.y == value and point.handle_right.y == value
               for point in curve.keyframe_points)


def _linear_noise(curve, neutral, maximum):
    """A linear channel stays within its endpoint envelope over the clip."""
    if (curve.modifiers or curve.sampled_points or curve.extrapolation != 'CONSTANT'
            or not curve.keyframe_points):
        return False
    return all(point.interpolation in {'LINEAR', 'CONSTANT'} and
               abs(float(point.co.y) - neutral) <= maximum for point in curve.keyframe_points)


def _audit_excluded_source(context, source, plan, *, progress=None, daily_fk=False):
    """Refuse independent secondary motion that this body route cannot retain.

    A neutral bone is evaluated from its own Rest and its *animated* parent;
    inherited body motion is therefore retained by the current hierarchy, not
    mistaken for an independently authored hair/skirt channel. Both the normal
    native gate and the existing world playback budget bound numerical noise.
    """
    from mathutils import Matrix
    from . import animation_export_inputs as inputs, controller_animation as controller
    names = tuple(plan.diagnostics['excluded_source_bones'])
    curves = inputs._curves(plan.source_action, plan.source_slot)
    by_bone = {}
    for curve in curves:
        by_bone.setdefault(controller._bone_name(curve.data_path), []).append(curve)
    animated = set(by_bone) & set(names)
    for name in animated:
        bone = source.pose.bones[name]
        rotation = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                    'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        paths = {bone.path_from_id(field) for field in ('location', rotation, 'scale')}
        _require(all(curve.data_path in paths for curve in by_bone[name]),
                 'Additional native source animation needs a supported mapping for ' + name + '.')
    extra_times = (_source_channels(plan.source_rig, plan.source_action, plan.source_slot, animated)
                   if animated else ())
    times = tuple(sorted(set(plan.source_times) | set(extra_times)))
    _require(len(times) <= MAX_SAMPLES, 'Secondary source verification exceeds the mapping sample bound.')
    policy = controller._quality_policy(context, 'EDITABLE')
    worst_native = dict(matrix=0., position=0., rotation=0., scale=0.)
    worst_world = dict(position_meters=0., rotation_radians=0., scale_relative=0., shape_relative=0.)
    failures = {}
    checked_names = names
    roundoff_names = ()
    if daily_fk:
        from . import animation_controller_fk_metrics as metrics
        # _source_copy explicitly sets every basis to identity. _prepare
        # excludes source drivers and constraints. An unanimated bone or an
        # exactly neutral transform therefore follows its animated parent.
        checked_names = tuple(name for name in names if name in animated and
                              not all(_identity_curve(source.pose.bones[name], curve)
                                      for curve in by_bone[name]))
        # Unity writes tiny float residuals on nominally rest hair/dress.
        # Accept that local input error only on ordinary, near-rigid skeletons;
        # large units, scaled parents and nonlinear or non-neutral inputs keep
        # the complete evaluated verification below.
        limit = 3.e-6
        meters = plan.source_world.to_scale().length * policy['scene_meters_per_unit']
        if (meters <= 2. and all(len(b.parent_recursive) < 50 and b.inherit_scale == 'FULL' and
                                b.use_inherit_rotation and b.use_local_location for b in source.data.bones)
                and all(_linear_noise(curve, 1., limit) for curve in curves if curve.data_path.endswith('.scale'))):
            def within_noise(name):
                bone = source.pose.bones[name]
                if bone.rotation_mode != 'QUATERNION':
                    return False
                return all(_linear_noise(f, float(f.data_path.endswith('.scale') or
                           (f.data_path.endswith('.rotation_quaternion') and f.array_index == 0)), limit)
                           for f in by_bone[name])
            roundoff_names = tuple(n for n in checked_names if within_noise(n))
        roundoff = set(roundoff_names)
        checked_names = tuple(n for n in checked_names if n not in roundoff)
    probes = controller._probes(times) if checked_names else ()
    # Independently replay extremities and quarter-clip checkpoints as a guard
    # against an unexpected rig or unit interpretation of bounded local inputs.
    checkpoints = {times[round(i*(len(times)-1)/4)] for i in range(5)} if roundoff_names else set()
    probes = tuple(sorted(set(probes) | checkpoints))
    for index, frame in enumerate(probes, 1):
        if progress is not None:
            progress('Checking secondary bones', index, len(probes))
        controller._frame(context.scene, frame)
        context.view_layer.update()
        evaluated = source.evaluated_get(context.evaluated_depsgraph_get())
        native_poses = []
        tested_names = (*checked_names, *roundoff_names) if frame in checkpoints else checked_names
        for name in tested_names:
            bone = source.data.bones[name]
            kwargs = (dict(parent_matrix=evaluated.pose.bones[bone.parent.name].matrix,
                           parent_matrix_local=bone.parent.matrix_local) if bone.parent else {})
            neutral = bone.convert_local_to_pose(Matrix.Identity(4), bone.matrix_local, **kwargs)
            native_poses.append((name, evaluated.pose.bones[name].matrix.copy(), neutral))
        if daily_fk:
            native, world = metrics.compare(plan.source_world, [value for _, value, _ in native_poses],
                                            plan.source_world, [rest for _, _, rest in native_poses],
                                            policy['scene_meters_per_unit'], native=True)
            for key in worst_native:
                worst_native[key] = max(worst_native[key], native[key])
            for key in worst_world:
                worst_world[key] = max(worst_world[key], world[key])
            if controller._acceptable(native) and controller._playback_acceptable(world, policy):
                continue
        for name, pose, neutral in native_poses:
            actual = (plan.source_world, {name: pose})
            expected = (plan.source_world, {name: neutral})
            native = controller._error(actual, expected)
            world = controller._world_playback_error(actual, expected, policy['scene_meters_per_unit'])
            for key in worst_native:
                worst_native[key] = max(worst_native[key], native[key])
            for key in worst_world:
                worst_world[key] = max(worst_world[key], world[key])
            if not controller._acceptable(native) or not controller._playback_acceptable(world, policy):
                failures.setdefault(name, dict(source_frame=frame, native=native, world=world))
    proof = dict(bone_count=len(names), animated_bone_count=len(animated), sample_count=len(times),
                 probe_count=len(probes), native_maximum=worst_native, world_maximum=worst_world,
                 playback_policy=policy, native_gate_also_required=True,
                 reference='own Rest with the currently evaluated source parent; no artist-held pose',
                 independent_secondary_animation='refused when outside either existing native or world gate',
                 verified_as_rest=not failures)
    if daily_fk:
        proof.update(structurally_neutral_bone_count=len(names)-len(checked_names)-len(roundoff_names),
                     bounded_local_noise_bone_count=len(roundoff_names),
                     bounded_local_noise_per_component=3.e-6, bounded_noise_native_times=sorted(checkpoints),
                     full_secondary_probe_bone_count=len(checked_names),
                     independent_secondary_animation=(
                         'Identity inputs are inherited. Bounded local import noise is checked at clip '
                         'checkpoints. All other animated secondary bones use the full native/world probe grid.'))
    if failures:
        error = MappingNeeded('This source contains native secondary motion or pose without a supported target mapping: '
                              + ', '.join(sorted(failures)) + '. The original motion was retained.')
        error.diagnostics = dict(excluded_source_verification=proof, nonneutral_bones=failures)
        raise error
    return proof


def _fk_basis_order(context, target, control_plan):
    """Compile neutral helper parents and authenticated full FK-space copies."""
    output = {control_plan.redirects.get(name, name): name for name in control_plan.mapped_names}
    evaluated = target.evaluated_get(context.evaluated_depsgraph_get())
    parents = {}
    def add_parent(pb, visiting):
        if pb is None or pb.name in output or pb.name in control_plan.mapped_names or pb.name in parents:
            return True
        if pb.name not in control_plan.control_names or pb.name in visiting:
            return False
        active = [c for c in evaluated.pose.bones[pb.name].constraints if not c.mute and c.influence != 0.]
        copy = None
        if active:
            c = active[-1]
            owned = (pb.name, c.name) in control_plan.constraint_keys
            if (owned and c.type == 'COPY_TRANSFORMS' and c.influence == 1. and c.mix_mode == 'REPLACE'
                    and c.owner_space == c.target_space == 'WORLD' and c.subtarget
                    and c.target and c.target.original == target and c.head_tail == 0.
                    and not c.remove_target_shear):
                copy = c.subtarget
            elif not all((pb.name, con.name) in control_plan.constraint_keys and con.type == 'COPY_ROTATION'
                         and con.owner_space == con.target_space == 'LOCAL' and con.mix_mode == 'AFTER'
                         and con.target and con.target.original == target
                         and con.subtarget in control_plan.neutral for con in active):
                return False
        peer = target.pose.bones[copy] if copy else pb.parent
        if not add_parent(peer, visiting | {pb.name}):
            return False
        parents[pb.name] = (pb.bone, copy)
        return True
    writes = tuple((target.pose.bones[name], source) for name, source in output.items())
    eyes = tuple((target.pose.bones[control], name, distance)
                 for name, (control, distance) in control_plan.eyes.items())
    if (not all(add_parent(pb.parent, set()) for pb, _ in writes)
            or not all(not pb.constraints and add_parent(pb.parent, set()) for pb, _, _ in eyes)):
        return None
    return tuple(parents.values()), writes, eyes


def _set_fk_bases(capture_rig, order, desired):
    """Calculate FK and head-relative gaze as editable local channel bases."""
    from mathutils import Matrix
    parents, writes, eyes = order
    poses = dict(desired)
    poses.update((pb.name, desired[source]) for pb, source in writes)
    identity = Matrix.Identity(4)
    def convert(bone, matrix, invert=False):
        if bone.parent:
            return bone.convert_local_to_pose(matrix, bone.matrix_local, invert=invert,
                        parent_matrix=poses[bone.parent.name], parent_matrix_local=bone.parent.matrix_local)
        return bone.convert_local_to_pose(matrix, bone.matrix_local, invert=invert)
    for bone, copy in parents:
        poses[bone.name] = poses[copy] if copy else convert(bone, identity)
    for pb, source in writes:
        capture_rig.pose.bones[pb.name].matrix_basis = convert(pb.bone, desired[source], invert=True)
    for pb, source, distance in eyes:
        axis = desired[source].to_3x3().col[1]
        _require(axis.length > 1.e-8, 'An eye motion has no valid aiming axis.')
        matrix = convert(pb.bone, identity)
        matrix.translation = desired[source].translation + axis.normalized() * distance
        capture_rig.pose.bones[pb.name].matrix_basis = convert(pb.bone, matrix, invert=True)


def _set_desired(context, target, control_plan, desired):
    from . import unity_animation_controls as controls
    order = sorted(control_plan.mapped_names, key=lambda name: len(target.data.bones[name].parent_recursive))
    post_redirect_updates = controls._post_redirect_updates(target, control_plan, order)
    for name in order:
        if name in control_plan.redirects:
            context.view_layer.update()
            target.pose.bones[control_plan.redirects[name]].matrix = desired[name]
            if name in post_redirect_updates:
                context.view_layer.update()
        else:
            target.pose.bones[name].matrix_basis = controls._basis_for_native(target.pose.bones[name], desired, control_plan)
    context.view_layer.update()
    for name, (control, distance) in control_plan.eyes.items():
        axis = desired[name].to_3x3().col[1]
        _require(axis.length > 1.e-8, 'An eye motion has no valid aiming axis.')
        pose = target.pose.bones[control]
        matrix = pose.matrix.copy()
        matrix.translation = desired[name].translation + axis.normalized() * distance
        pose.matrix = matrix
    context.view_layer.update()


def _output_time(plan, frame):
    return 1. + (frame - plan.source_times[0]) * plan.diagnostics['target_fps'] / plan.diagnostics['source_fps']


def _verification_times(plan, reference_times, controller_times):
    """Keep both native grids in source time for the independent oracle.

    Controller refinement uses target-scene frames. Every such matched time
    must also be compared directly with the original source, including when
    source and target frame rates differ. The caller's normal quarter probes
    then cover each interval of this complete union.
    """
    ratio = plan.diagnostics['source_fps'] / plan.diagnostics['target_fps']
    first = plan.source_times[0]
    converted = []
    for frame in controller_times:
        _require(type(frame) in (int, float) and math.isfinite(frame),
                 'Controller verification needs finite native sample times.')
        converted.append(first + (frame - 1.) * ratio)
    _require(converted, 'Controller verification lost its native sample times.')
    return tuple(sorted(set(reference_times) | set(converted)))


def _write_reference(action, target, channels, times, *, compact=False, switches=()):
    from . import animation_retarget as retarget
    slot, bag = retarget._new_channelbag(action, target)
    for channel_number, ((path, index), values) in enumerate(channels.items()):
        if compact and all(value == values[0] for value in values):
            # Keep the clip bounds even for a motionless clip. Capture order
            # starts with transforms; each static IK/FK property gets one key.
            if channel_number == 0:
                retarget._write_curve(bag, path, index, (times[0], times[-1]), (values[0], values[-1]))
            else:
                retarget._write_curve(bag, path, index, times[:1], values[:1])
        else:
            retarget._write_curve(bag, path, index, times, values)
    if switches:
        for curve in bag.fcurves:
            if (curve.data_path, curve.array_index) in switches:
                for point in curve.keyframe_points:
                    point.interpolation = 'CONSTANT'
    return slot.handle


def _daily_source_curves(plan):
    """Only admit active source TRS and interpolation representable by the linear FK sampler."""
    from itertools import islice
    from . import animation_export_inputs as inputs
    paths = {}
    rotations = {}
    for bone in plan.source_rig.pose.bones:
        prop = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        for field in ('location', prop, 'scale'):
            paths[bone.path_from_id(field)] = (bone.name, field, len(getattr(bone, field)))
    curves = inputs._curves(plan.source_action, plan.source_slot)
    for curve in curves:
        info = paths.get(curve.data_path)
        _require(info is not None and 0 <= curve.array_index < info[2],
                 'Fast FK needs an active transform on a source bone. Resolve or remove the unsupported '
                 'source curve ' + curve.data_path + '[' + str(curve.array_index) + '].')
        bone, field, size = info
        if field.startswith('rotation_') and bone in plan.names:
            rotations.setdefault((bone, field), [None] * size)[curve.array_index] = curve
        points = curve.keyframe_points
        _require(len(points) and not curve.sampled_points and not curve.modifiers,
                 'Bake the source to explicit LINEAR transform keys before preparing ' + bone + '.')
        previous = points[0]
        for following in islice(points, 1, None):
            interpolation = previous.interpolation
            if interpolation != 'LINEAR':
                flat = previous.co.y == following.co.y
                if interpolation == 'BEZIER':
                    flat = flat and previous.handle_right.y == previous.co.y and following.handle_left.y == following.co.y
                elif interpolation == 'ELASTIC':
                    flat = flat and previous.amplitude == 0.
                _require(flat, 'Bake the source to LINEAR transform keys before preparing '
                         + bone + '. ' + interpolation + ' interpolation near frame '
                         + str(float(previous.co.x)) + ' can change the motion between FK keys.')
            previous = following
    return curves, rotations


def _daily_rotation_arcs(plan, rotations, anchors):
    """Refuse under-sampled turns before FK chooses its nearest quaternion/Euler branch."""
    import numpy as np
    times = plan.source_times
    initial = frozenset(anchors)
    selected = [i for i, frame in enumerate(times) if frame in initial]
    for (name, prop), curves in rotations.items():
        _require(all(curve is not None for curve in curves), 'Source rotation channels are incomplete for ' + name + '.')
        # Check both authored/intermediate times and the actual FK key grid:
        # several collinear source keys can complete a full turn in one frame.
        values = np.asarray([[curve.evaluate(t) for curve in curves] for t in times], dtype=np.float64)
        def unresolved(rows):
            if prop == 'rotation_quaternion':
                lengths = np.linalg.norm(rows, axis=1)
                _require(np.isfinite(lengths).all() and np.min(lengths) > 1.e-6,
                         'Bake a continuous normalized quaternion rotation for ' + name + ' before preparing FK.')
                units = rows / lengths[:, None]
                return np.sum(units[1:] * units[:-1], axis=1) <= 0.
            if prop == 'rotation_euler':
                return np.sum(np.abs(np.diff(rows, axis=0)), axis=1) >= math.pi
            axes = rows[:, 1:]
            lengths = np.linalg.norm(axes, axis=1)
            rotating = np.abs(rows[:, 0]) > 1.e-7
            _require(not np.any(rotating & (lengths < 1.e-6)),
                     'Bake a valid axis-angle rotation for ' + name + ' before preparing FK.')
            units = axes / np.maximum(lengths[:, None], 1.e-12)
            changing_axis = np.sum(units[:-1] * units[1:], axis=1) <= 0.
            return ((np.abs(np.diff(rows[:, 0])) >= math.pi) | (changing_axis & (rotating[:-1] | rotating[1:])))
        bad = np.flatnonzero(unresolved(values))
        if len(bad):
            frame = times[bad[0]]
            raise MappingNeeded(f'Bake denser LINEAR rotation keys for {name} after frame {frame:g} '
                                'and make quaternion signs continuous before preparing FK.')
        if len(selected) < len(times):
            for i in np.flatnonzero(unresolved(values[selected])):
                anchors.update(times[selected[i]:selected[i + 1] + 1])


def _daily_fk_grid(plan):
    """Retain authored turns between whole frames and spread native checks over the clip."""
    from bisect import bisect_right
    curves, rotations = _daily_source_curves(plan)
    frame_times = tuple(t for t in plan.source_times if t.is_integer() or t in (plan.source_times[0], plan.source_times[-1]))
    anchors = set(frame_times)
    fractions = {}
    for t in plan.source_times:
        if t not in anchors:
            i = bisect_right(frame_times, t)
            a, b = frame_times[i - 1:i + 1]
            fractions[t] = (a, b, (t-a)/(b-a))
    # Use source curves only to select extra key times. The published Action
    # is independently checked against native source and target evaluations.
    scores = dict.fromkeys(fractions, 0.)
    paths = {plan.source_rig.pose.bones[n].path_from_id(prop) for n in plan.names
             for prop in ('rotation_euler', 'rotation_axis_angle', 'rotation_quaternion', 'location', 'scale')}
    for curve in curves:
        if not fractions or curve.data_path not in paths:
            continue
        values = {t: curve.evaluate(t) for t in frame_times}
        for t, (a, b, weight) in fractions.items():
            delta = abs(curve.evaluate(t) - (values[a] + (values[b] - values[a]) * weight))
            scores[t] = max(delta, scores[t])
    anchors.update(t for t, score in scores.items() if score > .0005)
    _daily_rotation_arcs(plan, rotations, anchors)
    return tuple(sorted(anchors)), scores


def _daily_fk_probes(plan, samples, scores, prior_failures):
    """Sample keyed poses, unkeyed source times and intervals; revisit previous failures."""
    def spread(values, count):
        values = sorted(values)
        if len(values) <= count:
            return values
        return [values[round(i*(len(values)-1)/(count-1))] for i in range(count)]
    domain = sorted(set(plan.source_times) | set(samples))
    unkeyed = set(domain) - set(samples)
    midpoints = [(a + b) * .5 for a, b in zip(domain, domain[1:])]
    probes = set(spread(samples, 12)) | set(spread(unkeyed, 18)) | set(spread(midpoints, 24))
    probes.update(sorted(unkeyed, key=lambda t: (-scores.get(t, 0.), t))[:12])
    probes.update(prior_failures)
    return tuple(sorted(probes))


def _fk_capture_plan(capture):
    """Capture complete temporary rotations for chronological branch continuity, even on driven axes."""
    bones, rotation_paths, omitted = [], {}, set()
    for name, connected, mode, (location, rotation, scale) in capture[0]:
        if rotation and mode != 'AXIS_ANGLE':
            path = rotation[0][1][0]
            rotation_paths[path] = mode
            full = tuple((i, (path, i)) for i in range(4 if mode == 'QUATERNION' else 3))
            omitted.update(key for axis, key in full if (axis, key) not in rotation)
            rotation = full
        bones.append((name, connected, mode, (location, rotation, scale)))
    return (tuple(bones), capture[1]), rotation_paths, omitted


def _verify_mapping(context, source, target, plan, action, slot, times, *, strict_times=None,
                    progress=None, round_number=0, daily_edit=False, fk_cache=None, fk_scores=None, prior_failures=()):
    from . import controller_animation as controller
    animation = target.animation_data_create()
    animation.action = action
    animation.action_slot = next(value for value in action.slots if value.handle == slot)
    animation.use_nla = False
    animation.action_blend_type, animation.action_influence = 'REPLACE', 1.
    policy = controller._daily_edit_policy(context) if daily_edit else controller._quality_policy(context, 'EDITABLE')
    failures, worst_matrix, worst_position, worst_rotation = [], 0., 0., 0.
    samples = set(times if strict_times is None else strict_times)
    domain = tuple(sorted(set(times) | samples))
    # Keep the original source interval probes as well as the refined grid.
    # New controller samples are still source intervals, not new authored keys.
    if fk_cache is None:
        probes = tuple(sorted(set(controller._probes(domain))
                              | set(controller._probes(tuple(sorted(samples))))))
    else:
        from . import animation_controller_fk_metrics as metrics
        domain = tuple(sorted(set(plan.source_times) | set(domain)))
        probes = _daily_fk_probes(plan, samples, fk_scores, prior_failures)
    phase = 'Checking mapped poses' if strict_times is None else 'Checking final motion'
    for index, frame in enumerate(probes, 1):
        if progress is not None:
            progress(phase, index, len(probes), round_number)
        desired = fk_cache.get(frame) if fk_cache is not None else None
        from_cache = desired is not None
        if not from_cache:
            desired = _desired(context, source, plan, frame)
            if fk_cache is not None and len(fk_cache) < 512:
                fk_cache[frame] = desired
        expected = (plan.target_world, desired)
        output_frame = _output_time(plan, frame)
        if fk_cache is None or from_cache or output_frame != frame:
            controller._frame(context.scene, output_frame)
            context.view_layer.update()
        actual = controller._pose(context, target, plan.names)
        if fk_cache is None:
            errors = controller._error(actual, expected)
            world = controller._world_playback_error(actual, expected, policy['scene_meters_per_unit'])
        else:
            errors, world = metrics.compare(actual[0], [actual[1][n] for n in plan.names],
                                            expected[0], [desired[n] for n in plan.names],
                                            policy['scene_meters_per_unit'], native=True)
        worst_matrix = max(worst_matrix, errors['matrix'])
        worst_position = max(worst_position, world['position_meters'])
        worst_rotation = max(worst_rotation, world['rotation_radians'])
        acceptable = (controller._acceptable(errors) if frame in samples and not daily_edit else
                      controller._playback_acceptable(world, policy))
        if not acceptable:
            failures.append(frame)
    return failures, dict(sample_count=len(samples), validation_grid_sample_count=len(domain),
                          probe_count=len(probes), strict_source_times=[] if daily_edit else sorted(samples),
                          verification_source_times=list(probes),
                          maximum_matrix_error=worst_matrix,
                          maximum_position_meters=worst_position,
                          maximum_rotation_radians=worst_rotation,
                          playback_policy=policy)


def _build_private(context, source_rig, source_action, source_slot, target, *, source_fps=None, progress=None,
                   daily_edit=False, action_hash=None):
    import bpy
    from . import controller_animation as controller, unity_animation_controls as controls
    plan = prepare(context, source_rig, source_action, source_slot, target, source_fps=source_fps,
                   action_hash=action_hash)
    control_plan = _body_control_plan(plan)
    source_copy = source_data = reference = result_action = None
    complete = False
    try:
        source_copy, source_data = _source_copy(context.scene, plan)
        plan.diagnostics['excluded_source_verification'] = _audit_excluded_source(
            context, source_copy, plan, progress=progress, daily_fk=daily_edit)
        times = plan.source_times
        fk_cache = {} if daily_edit else None
        fk_scores = {}
        if daily_edit:
            times, fk_scores = _daily_fk_grid(plan)
        keyed = tuple(sorted((set(plan.names) - set(control_plan.redirects))
                             | set(control_plan.redirects.values()) | plan.control_names))
        fk_order = None
        capture = controls._compile_channel_capture(target, control_plan, keyed)
        cached_indices, cached_channels, rotation_paths = {}, {}, {}
        fk_passes, prior_failures = [], set()
        omitted = set()
        reuse_channels = daily_edit
        if daily_edit:
            capture, rotation_paths, omitted = _fk_capture_plan(capture)
            # Axis-angle branch changes still require chronological capture.
            reuse_channels = not any(mode == 'AXIS_ANGLE' and dst[1] for _, _, mode, dst in capture[0])
        for attempt in range(controller.MAX_REFINEMENTS + 1):
            _require(len(times) <= MAX_SAMPLES, 'Mapping needs more native samples than this operation supports.')
            if target.animation_data:
                target.animation_data.action = None
            _neutral_target(context, target, control_plan)
            if daily_edit and attempt == 0:
                fk_order = _fk_basis_order(context, target, control_plan)
            capture_rig = target
            if fk_order is not None:
                # Rest and static driver channels come from the evaluated
                # neutral target. Only mapped FK and gaze bases vary.
                from types import SimpleNamespace
                bones = {name: SimpleNamespace(matrix_basis=target.pose.bones[name].matrix_basis.copy())
                         for name in keyed}
                capture_rig = SimpleNamespace(pose=SimpleNamespace(bones=bones))
            channels, previous_q, previous_e = {}, {}, {}
            solve_times = tuple(t for t in times if t not in cached_indices) if reuse_channels else times
            for index, frame in enumerate(solve_times, 1):
                if progress is not None:
                    progress('Mapping body poses', index, len(solve_times), attempt + 1)
                desired = fk_cache.get(frame) if daily_edit else None
                if desired is None:
                    desired = _desired(context, source_copy, plan, frame)
                    if daily_edit and len(fk_cache) < 512:
                        fk_cache[frame] = desired
                if fk_order is not None:
                    _set_fk_bases(capture_rig, fk_order, desired)
                else:
                    if daily_edit:
                        from mathutils import Matrix
                        for name, _ in control_plan.eyes.values():
                            target.pose.bones[name].matrix_basis = Matrix.Identity(4)
                    else:
                        _neutral_target(context, target, control_plan)
                    _set_desired(context, target, control_plan, desired)
                if not daily_edit:
                    actual = controller._pose(context, target, plan.names)
                    errors = controller._error(actual, (plan.target_world, desired))
                if not daily_edit and not controller._acceptable(errors):
                    per_bone = {name: controller._error((actual[0], {name: actual[1][name]}),
                                (plan.target_world, {name: desired[name]})) for name in plan.names}
                    largest = sorted(per_bone, key=lambda name: per_bone[name]['matrix'], reverse=True)[:4]
                    details = ', '.join(f'{name} matrix={per_bone[name]["matrix"]:.6g} '
                                        f'position={per_bone[name]["position"]:.6g} '
                                        f'rotation={per_bone[name]["rotation"]:.6g}' for name in largest)
                    error = MappingNeeded(f'The current controls cannot reproduce mapped body frame {frame:g}. ' + details)
                    error.diagnostics = dict(frame=frame, errors=errors, per_bone=per_bone)
                    raise error
                if reuse_channels:
                    cached_indices[frame] = len(cached_indices)
                    controls._capture_channels(target, capture_rig, control_plan, keyed, cached_channels,
                                               {}, {}, capture_plan=capture)
                else:
                    controls._capture_channels(target, capture_rig, control_plan, keyed, channels,
                                               previous_q, previous_e, capture_plan=capture)
            if reuse_channels:
                channels = controller._order_solved_channels(cached_channels, times, cached_indices, rotation_paths)
            for key in omitted:
                channels.pop(key, None)
            reference = bpy.data.actions.new('Mapped Body Reference')
            switches = {key for key, _ in capture[1]} if daily_edit else ()
            slot = _write_reference(reference, target, channels,
                                    tuple(_output_time(plan, frame) for frame in times),
                                    compact=daily_edit, switches=switches)
            failed, reference_proof = _verify_mapping(context, source_copy, target, plan, reference, slot, times,
                                                       progress=progress, round_number=attempt + 1,
                                                       daily_edit=daily_edit, fk_cache=fk_cache, fk_scores=fk_scores,
                                                       prior_failures=prior_failures)
            if daily_edit:
                fk_passes.append(dict(mapped=len(solve_times), keys=len(times), checked=reference_proof['probe_count'],
                                      failed=len(failed), keyed_failures=len(set(failed) & set(times)),
                                      direct_fk=fk_order is not None, reuse_channels=reuse_channels))
            if not failed:
                break
            prior_failures.update(failed)
            target.animation_data.action = None
            bpy.data.actions.remove(reference)
            reference = None
            if fk_order is not None and set(failed) & set(times):
                # Constrained rigs can evaluate a redirect differently from
                # its direct FK basis. Rebuild these channels with the native
                # setters before accepting any portion of the candidate.
                fk_order = None
                cached_indices.clear()
                cached_channels.clear()
                _require(attempt < controller.MAX_REFINEMENTS,
                         'The target needs native FK mapping beyond the available refinement passes.')
                times = tuple(sorted(set(times) | set(failed)))
                continue
            _require(attempt < controller.MAX_REFINEMENTS and (not daily_edit or set(failed) - set(times)),
                     'The mapped FK reference cannot reproduce the original source subframes within tolerance.')
            times = tuple(sorted(set(times) | set(failed)))
        if daily_edit:
            # Publish the very same target-local FK Action whose native playback
            # was checked. Do not bake idle IK effectors through the clip.
            target.animation_data.action = None
            result_action, reference = reference, None
            result_action.name = source_action.name + ' · X FK'
            result_action['character_designer_controller_representation'] = 'FK'
            final_proof = reference_proof
            verification_times = final_proof['verification_source_times']
            conversion = dict(control_representation='FK', keyed_samples=len(times), whole_clip_ik=False,
                              passes=fk_passes)
        else:
            ids_before_conversion = set(bpy.data.user_map())
            try:
                built = controller.build_controller_channels(context, target, reference, slot, pose_matching=True,
                                                             progress=progress)
            except ValueError as error:
                if set(bpy.data.user_map()) != ids_before_conversion:
                    raise RuntimeError('Failed controller conversion retained private IDs requiring recovery.') from error
                raise MappingNeeded('Mapped body controller playback needs additional support: ' + str(error)) from error
            _require(built.get('conversion_state') == 'CONTROLLER', 'The mapped body did not produce controller animation.')
            target.animation_data.action = None
            result_action = reference.copy()
            result_action.name = source_action.name + ' · X Controller'
            result_action.use_fake_user = False
            controller.apply_to_action(result_action, slot, built)
            verification_times = _verification_times(plan, times, built['frame_times'])
            failed, final_proof = _verify_mapping(context, source_copy, target, plan,
                                                  result_action, slot, verification_times, strict_times=times,
                                                  progress=progress)
            if failed:
                error = MappingNeeded('The generated controller Action missed the independently mapped source playback tolerance.')
                error.diagnostics = dict(failed_source_times=list(failed), playback=final_proof,
                                         verification_source_times=list(verification_times))
                raise error
            target.animation_data.action = None
            conversion = built['diagnostics']
        offset, api, _match, _limb, _inputs = controller._api()
        final_hash = (action_hash(source_action, source_slot) if action_hash
                      else offset._action_hash(api, source_action, source_slot))
        _require(final_hash == plan.source_fingerprint
                 and _rest_fingerprint(source_rig) == plan.diagnostics['source_rest_sha256'],
                 'The source motion or its Rest changed during controller mapping.')
        _require(_rest_fingerprint(target) == plan.target_fingerprint,
                 'The target Rest changed during controller mapping.')
        diagnostics = dict(plan.diagnostics, state='CONTROLLER', native_output_verified=True,
                           reference_playback=reference_proof, controller_playback=final_proof,
                           controller_conversion=conversion, refinement_passes=attempt,
                           verification_source_times=list(verification_times),
                           strict_source_times=[] if daily_edit else list(times),
                           verification_domain=('World playback budget at spread keyed poses, independent source keys, interval midpoints, and previous failures'
                               if daily_edit else 'Reference source samples remain strict; intervals use the playback budget'))
        if daily_edit:
            diagnostics.update(control_representation='FK', whole_clip_ik=False)
        result_action[RECORD] = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':'), allow_nan=False)
        _require(result_action.users == 0, 'The new controller Action gained an unexpected user.')
        complete = True
        result = MappingResult(result_action, slot, plan.source_fingerprint, diagnostics)
    finally:
        try:
            if target.animation_data and target.animation_data.action in (reference, result_action):
                target.animation_data.action = None
            if source_copy is not None:
                bpy.data.objects.remove(source_copy, do_unlink=True)
            if source_data is not None:
                if source_data.users:
                    raise RuntimeError('The private mapping source data gained an unexpected user.')
                bpy.data.armatures.remove(source_data)
            if reference is not None:
                if reference.users:
                    raise RuntimeError('The private mapped reference Action gained an unexpected user.')
                bpy.data.actions.remove(reference)
        except BaseException:
            complete = False
            raise
        finally:
            if result_action is not None and not complete:
                if result_action.users:
                    raise RuntimeError('The incomplete mapped result gained an unexpected user.')
                bpy.data.actions.remove(result_action)
    return result


def _build_isolated(context, source_rig, source_action, source_slot, target_rig, *, source_fps=None, progress=None,
                    daily_edit=False, action_hash=None):
    """Worker-only native build; other rigs in this process may be evaluated.

    Blender can write Action inputs to originals while another scene is being
    sampled. The caller therefore owns an independent background process and
    must publish only the verified Action, never this worker's scene state.
    """
    import bpy
    if not bpy.app.background:
        raise RuntimeError('Native controller mapping requires its independent background worker.')
    from .animation_controller_isolation import _isolate_target, IsolationError
    from . import controller_animation as controller, forearm_twist
    controller._require_private_evaluation_idle()
    result = None
    try:
        try:
            with forearm_twist.defer_runtime(context, flush_on_exit=False):
                with _isolate_target(context, target_rig) as private:
                    result = _build_private(private.context, source_rig, source_action, source_slot,
                                            private.rig, source_fps=source_fps, progress=progress, daily_edit=daily_edit,
                                            action_hash=action_hash)
                    result.diagnostics['isolation'] = dict(private.diagnostics)
        except IsolationError as error:
            raise MappingNeeded('Controller mapping needs an isolated target graph: ' + str(error)) from error
        result.action[RECORD] = json.dumps(result.diagnostics, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':'), allow_nan=False)
        return result
    except Exception:
        if result is not None:
            if result.action.users:
                raise RuntimeError('A result Action survived failed isolation with an unexpected user.')
            bpy.data.actions.remove(result.action)
        raise


def build(context, source_rig, source_action, source_slot, target_rig, *, source_fps=None):
    """Return one verified unbound Action from an independent native worker."""
    from . import animation_controller_worker
    return animation_controller_worker.build(context, source_rig, source_action,
                                              source_slot, target_rig, source_fps=source_fps)
