"""Whole-clip body controller authoring, evaluated only on disposable copies.

The imported/native Action stays the read-only orientation input. Every sample
reads its evaluated raw RNA inputs, rather than the original object's held pose
or an inverse decomposition of constrained final bone matrices.
Hair/Dress branches are retained in the published Action, but are evaluated
neither as body outputs nor as private simulation dependencies.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re

RECORD = 'character_designer_controller_animation_v1'
SCHEMA = 'character-designer.controller-animation/1'
MAX_RECORD = 32768
MAX_BONES = 4096
MAX_KEYS = 500000
MAX_SAMPLES = 20001
MAX_BONE_SAMPLES = 1000000
MAX_REFINEMENTS = 4
MATRIX_TOLERANCE = 2.0e-4
_BONE_PATH = re.compile(r'^(?:data\.)?pose\.bones\[("(?:[^"\\]|\\.)*")\]|^data\.bones\[("(?:[^"\\]|\\.)*")\]')
_TRANSFORMS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')


def _require(value, message):
    if not value:
        raise ValueError(message)


def _require_private_evaluation_idle():
    """Other-scene seeks must not end an existing global Hair guard session."""
    import sys
    current = sys.modules.get(__package__ + '.hair_wiggle2_adapter')
    session = getattr(current, '_SESSION', None) if current is not None else None
    _require(session is None or isinstance(session, dict) and session.get('physics_paused') is True,
             'Pause Hair Physics before creating controller animation; the active Hair session was kept.')
    legacy = sys.modules.get(__package__ + '.hair_wiggle_adapter')
    _require(legacy is None or getattr(legacy, '_SESSION', None) is None,
             'End the legacy Hair preview before creating controller animation; its active session was kept.')


def _api():
    from . import control_pose_offset as offset, limb_ik_fk as match, limb_ik as limb
    from . import animation_export_inputs as inputs
    return offset, offset._modules(), match, limb, inputs


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _bone_name(path):
    found = _BONE_PATH.match(path)
    return ast.literal_eval(found[1] or found[2]) if found else None


def _read_record(action):
    raw = action.get(RECORD)
    if not isinstance(raw, str) or not 0 < len(raw) <= MAX_RECORD:
        return None
    try:
        value = json.loads(raw)
        if (type(value) is not dict or value.get('schema') != SCHEMA
                or type(value.get('frame_start')) not in (int, float)
                or type(value.get('frame_end')) not in (int, float)
                or not all(math.isfinite(value[key]) for key in ('frame_start', 'frame_end'))
                or type(value.get('limbs')) is not list or len(value['limbs']) > 4
                or any(type(name) is not str or len(name) > 16 for name in value['limbs'])):
            return None
        return value
    except (ValueError, TypeError, KeyError):
        return None


def status(context):
    """Bounded UI metadata only; execution always performs fresh admission."""
    from . import animation_worklist as worklist
    result = dict(state='UNAVAILABLE', message='Select an editable Custom animation.',
                  frame_start=None, frame_end=None, limbs=[], can_convert=False, has_record=False)
    item = worklist.current(context)
    if item is None or item.rig is None:
        return result
    if item.side != 'CUSTOM':
        result['message'] = 'Select Custom to convert.'
        return result
    rig, action = item.rig, item.custom_action
    ad = getattr(rig, 'animation_data', None)
    if (action is None or action.library or action.asset_data or ad is None or ad.action is not action
            or ad.action_slot is None or ad.action_slot.handle != item.custom_slot
            or getattr(item, 'new_animation', False) or getattr(item, 'composition_json', '')):
        return result
    value = _read_record(action)
    result.update(state='CONTROLLER' if value else 'CANDIDATE', can_convert=True,
                  has_record=value is not None,
                  message='Controller Animation' if value else 'Create complete body controller motion.')
    if value:
        result.update(frame_start=value['frame_start'], frame_end=value['frame_end'], limbs=value['limbs'])
    return result


def _secondary_bones(rig):
    """Authenticate registry membership; names/prefixes alone never exclude bones."""
    from . import skirt_rig as skirt, hair_bones_rig as hair
    import bpy
    result, hair_sources = set(), set()
    for source in skirt.shared_sources(rig):
        record = skirt.read_record(source)  # Proves complete shared native ownership/collection.
        _require(record is not None and skirt.is_shared(record), 'Restore the shared Dress ownership before conversion.')
        result.update(record['shared']['names'])
    for bone in rig.data.bones:
        if bone.get(skirt.OWNER_KEY):
            _require(bone.name in result, 'An unregistered Dress bone cannot be isolated from body conversion.')
        if bone.get(hair.OWNER_KEY) == hair.OWNER_VALUE:
            source = bone.get(hair.SOURCE_KEY)
            _require(isinstance(source, bpy.types.Object) and source.type == 'MESH'
                     and source.get(hair.RIG_KEY) is rig, 'Restore Hair source ownership before body conversion.')
            hair_sources.add(source)
    for source in hair_sources:
        record = hair._read_records(source)
        _require(record is not None, 'Restore the Hair chain registry before body conversion.')
        names = set()
        for chain in record['chains']:
            _require(len(chain['bones']) == chain['count'] == len(chain['rest']), 'The Hair registry is incomplete.')
            for name, rest in zip(chain['bones'], chain['rest']):
                bone = rig.data.bones.get(name)
                _require(bone is not None and bone.get(hair.OWNER_KEY) == hair.OWNER_VALUE
                         and bone.get(hair.SOURCE_KEY) is source
                         and bone.get(hair.SIGNATURE_KEY) == chain['signature'] and bone.use_deform
                         and hair._bone_state_matches(hair._bone_state(bone), rest)
                         and source.vertex_groups.get(name) is not None,
                         'A Hair bone no longer matches its source registry.')
                names.add(name)
        actual = {b.name for b in rig.data.bones if b.get(hair.OWNER_KEY) == hair.OWNER_VALUE
                  and b.get(hair.SOURCE_KEY) is source}
        _require(names == actual, 'Unregistered Hair bones cannot be isolated from body conversion.')
        result.update(names)
    return result


def _controls(rig, inventory):
    from . import root_control, torso_controls, spine_ik_fk, eye_controls, foot_controls
    from . import animation_worklist_target
    names = {bone.name for bone in inventory['bones']}
    view = animation_worklist_target.control_rest_view(rig)
    kwargs = {'source_rest': view} if view is not None else {}
    records = [module.validate(rig, inventory, **kwargs)
               for module in (root_control, torso_controls, spine_ik_fk, eye_controls)]
    records.extend(foot_controls.validate(rig, inventory, **kwargs).values())
    for record in records:
        if record:
            names.update(record['bones'].values())
    return names


def _constraint_dependencies(rig, constraint):
    """Only declared same-rig RNA inputs may participate in the body closure."""
    import bpy
    result = set()
    for prop in constraint.bl_rna.properties:
        if prop.identifier == 'rna_type':
            continue
        if prop.type == 'COLLECTION':
            _require(not len(getattr(constraint, prop.identifier)),
                     'A body constraint has nested target inputs; bake it before controller conversion.')
        elif prop.type == 'POINTER':
            value = getattr(constraint, prop.identifier)
            if isinstance(value, bpy.types.ID):
                _require(value in (rig, rig.data), 'An external constraint input affects the body; bake it first.')
    for field in ('subtarget', 'pole_subtarget', 'space_subtarget'):
        name = getattr(constraint, field, '')
        if name:
            _require(name in rig.pose.bones, 'A body constraint target bone is missing.')
            result.add(name)
    return result


def _driver_dependencies(rig, curve):
    driver, result = curve.driver, set()
    _require(not driver.use_self and (driver.type != 'SCRIPTED' or driver.is_simple_expression),
             'A body driver uses undeclared inputs; bake it before controller conversion.')
    for variable in driver.variables:
        for target in variable.targets:
            _require(target.id in (rig, rig.data), 'An external driver input affects the body; bake it first.')
            path = getattr(target, 'data_path', '')
            if path:
                # ID-property pointer chains can escape the rig despite id=self.
                # Admit only one raw RNA field or terminal numeric property;
                # intermediate Object/Scene/Action references need a bake.
                quoted = r'\["(?:[^"\\]|\\.)*"\]'
                prefix = r'(?:(?:data\.)?(?:pose\.)?bones' + quoted + r')?'
                raw = r'(?:\.?[A-Za-z_][A-Za-z_0-9]*(?:\[[0-9]+\])*)'
                custom = quoted
                constraint = r'\.constraints' + quoted + r'\.[A-Za-z_][A-Za-z_0-9]*(?:\[[0-9]+\])*'
                _require(re.fullmatch(prefix + r'(?:' + raw + '|' + custom + '|' + constraint + ')', path),
                         'A body driver reads an indirect ID path; bake that input first.')
            name = getattr(target, 'bone_target', '') or _bone_name(path)
            if name:
                _require(name in rig.pose.bones, 'A body driver target bone is missing.')
                result.add(name)
            elif target.id is rig and getattr(target, 'data_path', '').startswith('pose.bones['):
                raise ValueError('A body driver target path is ambiguous; bake it first.')
    return result


def _closure(rig, inventory, secondary, controls):
    native = set(rig.pose.bones.keys()) - secondary - controls
    for entry in inventory['rigs'].values():
        native.update(entry['chain'])
    _require(native, 'This character has no native body outputs.')
    needed = set(native)
    drivers = tuple(rig.animation_data.drivers) if rig.animation_data else ()
    active_drivers = set()
    changed = True
    while changed:
        previous = set(needed)
        for name in tuple(needed):
            pb = rig.pose.bones[name]
            if pb.parent:
                needed.add(pb.parent.name)
            bone = getattr(pb, 'bone', None)
            if bone is not None and bone.bbone_segments > 1:
                for side in ('start', 'end'):
                    handle = getattr(bone, 'bbone_custom_handle_'+side, None)
                    if handle is not None and getattr(bone, 'bbone_handle_type_'+side, 'AUTO') != 'AUTO':
                        _require(handle.name not in secondary | controls,
                                 'A body B-Bone uses a secondary/controller handle; its deformation needs a separate conversion proof.')
                        needed.add(handle.name)
                _require(not any(child.use_connect and child.name in secondary | controls for child in bone.children),
                         'A body B-Bone uses a secondary/controller child handle; bake that deformation first.')
            for constraint in pb.constraints:
                needed.update(_constraint_dependencies(rig, constraint))
        # An IK constraint on a descendant also solves the parent chain.
        for pb in rig.pose.bones:
            for constraint in pb.constraints:
                if constraint.type in {'IK', 'SPLINE_IK'}:
                    chain, parent = {pb.name}, pb.parent
                    remaining = constraint.chain_count - 1 if constraint.chain_count else MAX_BONES
                    while parent is not None and remaining > 0:
                        chain.add(parent.name)
                        parent, remaining = parent.parent, remaining - 1
                    if chain & needed:
                        needed.add(pb.name)
                        needed.update(_constraint_dependencies(rig, constraint))
        for curve in drivers:
            owner = _bone_name(curve.data_path)
            if owner is None or owner in needed:
                active_drivers.add((curve.data_path, curve.array_index))
                needed.update(_driver_dependencies(rig, curve))
        _require(len(needed) <= MAX_BONES, 'Body dependency closure exceeds the supported bound.')
        changed = previous != needed
    return tuple(sorted(native)), frozenset(needed), frozenset(active_drivers)


def _times(action, curves, start, end, sample_step):
    _require(sample_step == 1.0, 'Controller conversion currently uses one-frame base sampling.')
    first, last = map(float, action.frame_range)
    _require(all(math.isfinite(v) for v in (first, last)) and last > first,
             'Choose an animation with a non-empty finite clip range.')
    _require((start is None or float(start) == first) and (end is None or float(end) == last),
             'Controller conversion currently requires the complete clip; an interval edit needs its own boundary policy.')
    _require(last - first < MAX_SAMPLES, 'This clip exceeds the controller sampling bound.')
    times = {first, last, *(float(frame) for frame in range(math.ceil(first), math.floor(last) + 1))}
    count = 0
    for curve in curves:
        count += len(curve.keyframe_points)
        _require(count <= MAX_KEYS, 'This clip exceeds 500,000 keys; use a shorter clip.')
        for key in curve.keyframe_points:
            frame = float(key.co.x)
            _require(math.isfinite(frame), 'The clip has a non-finite authored key time.')
            if first <= frame <= last:
                times.add(frame)
    _require(len(times) <= MAX_SAMPLES, 'This clip has too many controller sample times.')
    return tuple(sorted(times))


def _source_plan(context, rig, action, handle, start, end, sample_step, quality='EDITABLE'):
    policy = _quality_policy(context, quality)
    offset, api, match, limb, inputs = _api()
    _require(rig is not None and rig.type == 'ARMATURE' and not rig.library and not rig.data.library
             and rig.mode != 'EDIT' and rig.data.pose_position == 'POSE',
             'Controller conversion needs a local character in Pose Position outside Edit Mode.')
    _require(action is not None and not action.asset_data, 'Choose an animation Action, not a Pose asset.')
    _require(rig.parent is None and not rig.constraints and not rig.modifiers,
             'Object parenting, constraints or modifiers need a separate body conversion contract.')
    _require(0 < len(rig.pose.bones) <= MAX_BONES, 'This rig exceeds the controller bone bound.')
    _require(rig.data.animation_data is None, 'Animated Armature data needs a separate body conversion contract.')
    # This low-level API evaluates the explicit Action, not the host AnimData.
    # Imports legitimately restore an initial rig with no AnimData at all.
    # Current-Custom callers keep the stricter prepare_variant admission.
    handle = inputs._handle(rig, action, handle)
    curves = inputs._curves(action, handle)
    times = _times(action, curves, start, end, sample_step)
    inventory = limb._validate_inventory(rig)
    if not inventory['rigs']:
        return dict(conversion_state='NATIVE_FK', source_action=action, source_slot=handle,
                    channels={}, frame_times=times, native_names=(), metadata=None,
                    quality=quality, playback_policy=policy,
                    diagnostics={'reason': 'No installed limb controls; native FK was retained.'})
    _require(action.is_action_layered, 'Controller conversion needs a layered Action with an explicit Object slot.')
    _require('character_designer_rigging_session_v1' not in rig
             and not any(key in action for key in ('character_designer_rigging_capture_v1',
                                                   'character_designer_rigging_input_action_v1')),
             'Captured Rigging input uses its dedicated controller workflow.')
    for entry in inventory['rigs'].values():
        target = rig.pose.bones[entry['target'].name]
        _require(target.bone.get(match.VERSION_KEY) == match.VERSION,
                 'Update the installed Body IK/FK switching before converting controller motion.')
    secondary = _secondary_bones(rig)
    controls = _controls(rig, inventory)
    native, closure, drivers = _closure(rig, inventory, secondary, controls)
    from . import bone_collections
    display_path = '[' + json.dumps(bone_collections.AUTO_KEY) + ']'
    _require(not any(curve.data_path == 'data' + display_path for curve in curves),
             'Animated presentation metadata needs its own body conversion contract.')
    for curve in rig.animation_data.drivers if rig.animation_data else ():
        if (curve.data_path, curve.array_index) in drivers:
            _require(not any(target.id is rig.data and target.data_path == display_path
                             for variable in curve.driver.variables for target in variable.targets),
                     'A body driver reads presentation metadata; bake that input before conversion.')
    _require(len(native) * len(times) <= MAX_BONE_SAMPLES, 'This clip exceeds the body sample bound.')
    return dict(conversion_state='CONTROLLER', context=context, rig=rig, action=action, slot=handle,
                inventory=inventory, native_names=native, closure=closure, drivers=drivers,
                secondary=tuple(sorted(secondary)), frame_times=times,
                source_hash=offset._action_hash(api, action, handle), quality=quality, playback_policy=policy)


def _required_inputs(plan):
    """Missing raw inputs, sampled before constraints; existing curves survive."""
    from . import animation_export_inputs as inputs
    curves = {(curve.data_path, curve.array_index): curve
              for curve in inputs._curves(plan['action'], plan['slot'])}
    driven = {(curve.data_path, curve.array_index) for curve in plan['rig'].animation_data.drivers
              if not curve.mute} if plan['rig'].animation_data else set()
    required, generated = set(), set()
    for entry in plan['inventory']['rigs'].values():
        generated.update(entry[name].name for name in ('target', 'pole', 'ori_upper', 'ori_lower') if entry.get(name))
        if entry.get('foot_controls'):
            generated.add(entry['foot_controls']['toe_control'])
    for name in plan['closure'] - set(plan['secondary']):
        if name in generated:
            continue
        pb = plan['rig'].pose.bones[name]
        rotation = ('rotation_quaternion' if pb.rotation_mode == 'QUATERNION' else
                    'rotation_axis_angle' if pb.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        for field in ('location', rotation, 'scale'):
            required.update((pb.path_from_id(field), index) for index in range(len(getattr(pb, field))))
    # Root uniform scale and other declared numeric pose properties are raw
    # control inputs, not object placement. Do not key the driven scale itself.
    for curve in plan['rig'].animation_data.drivers if plan['rig'].animation_data else ():
        if (curve.data_path, curve.array_index) not in plan['drivers'] or curve.mute:
            continue
        for variable in curve.driver.variables:
            if variable.type != 'SINGLE_PROP':
                continue
            for target in variable.targets:
                path = target.data_path
                if (target.id is plan['rig'] and _bone_name(path) in plan['closure']
                        and re.fullmatch(r'pose\.bones\["(?:[^"\\]|\\.)*"\]\["(?:[^"\\]|\\.)*"\]', path)):
                    if path.endswith('["ik_fk"]') and _bone_name(path) in generated:
                        continue
                    value = plan['rig'].path_resolve(path)
                    _require(type(value) in (bool, int, float) and math.isfinite(value),
                             'A body control input is not a finite numeric scalar.')
                    required.add((path, 0))
    missing = []
    for key in sorted(required):
        if key in driven:
            continue
        curve = curves.get(key)
        if curve is not None:
            _require(not curve.mute and not getattr(getattr(curve, 'group', None), 'mute', False)
                     and (curve.keyframe_points or curve.sampled_points),
                     'A required body input has a disabled or empty curve; bake that input before conversion.')
        else:
            missing.append(key)
    return tuple(missing)


def _capture_inputs(source, keys, result):
    for path, index in keys:
        value = source.path_resolve(path)
        value = value[index] if hasattr(value, '__len__') and not isinstance(value, str) else value
        _require(type(value) in (bool, int, float) and math.isfinite(value),
                 'A sampled raw body input is not finite numeric data.')
        result.setdefault((path, index), []).append(float(value))


def _same_input_value(actual, expected):
    """Allow only small float32 evaluation/assignment noise, not pose error."""
    if not math.isfinite(actual) or not math.isfinite(expected):
        return False
    magnitude = max(abs(actual), abs(expected))
    exponent = math.frexp(magnitude)[1] if magnitude else -125
    quantum = max(2.**-149, math.ldexp(1., exponent-24))
    return abs(actual-expected) <= 4.*quantum


def _input_component(path, index=None):
    found = re.search(r'\[([0-9]+)\]$', path)
    return (path[:found.start()], int(found[1])) if found else (path, index)


def _raw_driver_inputs(plan):
    """Declared raw inputs of authenticated active body drivers, not outputs."""
    rig, result = plan['rig'], set()
    for curve in rig.animation_data.drivers if rig.animation_data else ():
        if curve.mute or (curve.data_path, curve.array_index) not in plan.get('drivers', ()):
            continue
        for variable in curve.driver.variables:
            if variable.type != 'SINGLE_PROP':
                continue
            for target in variable.targets:
                if target.id is rig:
                    path = target.data_path
                elif target.id is rig.data:
                    path = 'data' + ('' if target.data_path.startswith('[') else '.') + target.data_path
                else:
                    continue  # Fresh source admission rejects external body targets.
                result.add(_input_component(path))
    return result


def _pending_keyed_inputs(plan, frame):
    """Read current author RNA only; do not evaluate a scene or insert any key.

    A new Action binding evaluates its existing keyed inputs again. A manual
    override on those inputs must be committed first. Unkeyed defaults remain
    inputs to the independent reader; unrelated secondary simulation is outside
    this body admission check. This guard is intentionally not in the importer.
    """
    from . import animation_export_inputs as inputs
    rig, action = plan['rig'], plan['action']
    body = plan['closure'] - set(plan['secondary'])
    raw_inputs = _raw_driver_inputs(plan)
    raw_paths = {path for path, _index in raw_inputs}
    driven = {(curve.data_path, curve.array_index) for curve in rig.animation_data.drivers
              if not curve.mute} if rig.animation_data else set()
    pending, quaternions, seen = set(), {}, set()
    object_fields = set(_TRANSFORMS) | {'delta_location', 'delta_rotation_euler',
                                      'delta_rotation_quaternion', 'delta_scale'}
    for curve in inputs._curves(action, plan['slot']):
        if (curve.mute or getattr(getattr(curve, 'group', None), 'mute', False)
                or not (curve.keyframe_points or curve.sampled_points
                        or any(not getattr(mod, 'mute', False) for mod in curve.modifiers))):
            continue
        path, index = curve.data_path, curve.array_index
        if (path, index) in driven:
            continue
        name = _bone_name(path)
        component_path, component_index = _input_component(path, index)
        raw_dependency = ((component_path, component_index) in raw_inputs
                          or (component_path, None) in raw_inputs)
        # Do not hide a raw quaternion sign/magnitude change from a driver
        # behind orientation equivalence, including its other keyed components.
        raw_quaternion = component_path.endswith('rotation_quaternion') and component_path in raw_paths
        rotation_field = component_path.rsplit('.', 1)[-1]
        if name is not None:
            if path.startswith('data.bones['):
                # Only a declared body-driver input admits bone-data values;
                # unrelated presentation/secondary data is never inventoried.
                if not raw_dependency:
                    continue
                block, label = rig, name
            elif path.startswith('pose.bones['):
                if name not in body and not (raw_dependency or raw_quaternion):
                    continue
                block, label = rig.pose.bones[name], name
                if rotation_field in {'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle'}:
                    active = ('rotation_quaternion' if block.rotation_mode == 'QUATERNION' else
                              'rotation_axis_angle' if block.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
                    if rotation_field != active and not (raw_dependency or raw_quaternion):
                        continue
            else:
                continue
        elif path in object_fields:
            block, label = rig, 'Object ' + rig.name
            if path.startswith(('rotation_', 'delta_rotation_')):
                rotation = 'rotation_quaternion' if block.rotation_mode == 'QUATERNION' else (
                    'rotation_axis_angle' if block.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
                if path not in {rotation, 'delta_'+rotation} and not (raw_dependency or raw_quaternion):
                    continue
        elif raw_dependency:
            block, label = rig, 'Object ' + rig.name
        else:
            continue
        _require((component_path, component_index) not in seen, 'Duplicate keyed body inputs need a baked Action before conversion.')
        seen.add((component_path, component_index))
        try:
            value = rig.path_resolve(path)
            actual = value[index] if hasattr(value, '__len__') and not isinstance(value, str) else value
        except (ValueError, AttributeError, IndexError, KeyError, TypeError) as error:
            raise ValueError('A keyed body input is unavailable; choose a compatible Custom before converting.') from error
        if type(actual) not in (bool, int, float):
            continue
        expected = float(curve.evaluate(frame))
        _require(math.isfinite(expected) and math.isfinite(actual),
                 'A keyed body input is not finite; its current pose was kept.')
        quaternion = rotation_field in {'rotation_quaternion', 'delta_rotation_quaternion'}
        if quaternion and not raw_quaternion:
            _require(0 <= component_index < 4, 'A quaternion input has an invalid component.')
            vector = rig.path_resolve(component_path)
            group = quaternions.setdefault(component_path, dict(label=label, actual=tuple(vector), expected=list(vector)))
            group['expected'][component_index] = expected
        elif not (actual == expected if type(actual) in (bool, int) else _same_input_value(float(actual), expected)):
            pending.add(label)
    # Animation/RNA may normalize a quaternion or choose its opposite sign.
    # Compare the active represented rotation, not a harmless magnitude change.
    for group in quaternions.values():
        actual, expected = group['actual'], group['expected']
        a_length = math.sqrt(sum(component*component for component in actual))
        e_length = math.sqrt(sum(component*component for component in expected))
        _require(a_length > 1.e-8 and e_length > 1.e-8,
                 'A keyed quaternion input is invalid; its current pose was kept.')
        actual = tuple(component/a_length for component in actual)
        expected = tuple(component/e_length for component in expected)
        sign = -1. if sum(a*b for a, b in zip(actual, expected)) < 0. else 1.
        if any(abs(a-sign*b) > 4.*2.**-23 for a, b in zip(actual, expected)):
            pending.add(group['label'])
    return tuple(sorted(pending))


def _raw(rig, names):
    from . import animation_export_inputs as inputs
    def channels(block):
        settings = {key: getattr(block, key) for key in inputs._POSE_SETTINGS if hasattr(block, key)}
        if block is not rig:
            for prop in block.bl_rna.properties:
                if prop.identifier.startswith('bbone_') and not prop.is_readonly and prop.type in {'FLOAT', 'INT', 'BOOLEAN'}:
                    value = getattr(block, prop.identifier)
                    settings[prop.identifier] = tuple(value) if getattr(prop, 'is_array', False) else value
        return {'rotation_mode': block.rotation_mode, 'settings': settings,
                **{key: tuple(getattr(block, key)) for key in _TRANSFORMS},
                'properties': {str(key): value for key, value in block.items()
                               if type(value) in (bool, int, float) and math.isfinite(value)}}
    return dict(object=channels(rig), pose={name: channels(rig.pose.bones[name]) for name in names},
                constraints={(name, con.name): {key: value for key, value in inputs._settings(con).items() if key != 'name'}
                             for name in names for con in rig.pose.bones[name].constraints})


def _restore_raw(rig, raw):
    for block, state in [(rig, raw['object'])] + [(rig.pose.bones[name], state) for name, state in raw['pose'].items()]:
        block.rotation_mode = state['rotation_mode']
        for key in _TRANSFORMS:
            setattr(block, key, state[key])
        for key, value in state['settings'].items():
            setattr(block, key, value)
        for key, value in state['properties'].items():
            block[key] = value
    for (name, label), state in raw['constraints'].items():
        for key, value in state.items():
            setattr(rig.pose.bones[name].constraints[label], key, value)


def _remap(value, original, clone):
    import bpy
    if isinstance(value, bpy.types.Bone):
        return clone.data.bones[value.name]
    if isinstance(value, bpy.types.PoseBone):
        return clone.pose.bones[value.name]
    if isinstance(value, bpy.types.Constraint):
        return clone.path_resolve(value.path_from_id())
    if isinstance(value, dict):
        return {key: _remap(entry, original, clone) for key, entry in value.items()}
    if isinstance(value, tuple):
        return tuple(_remap(entry, original, clone) for entry in value)
    if isinstance(value, list):
        return [_remap(entry, original, clone) for entry in value]
    return clone if value is original else value


def _solver_inventory(plan, clone):
    """Drop only non-evaluated display constraint records before RNA remapping."""
    inventory, closure = plan['inventory'], plan['closure']
    def records(rows):
        return [row for row in rows if row[0].name in closure]
    projected = dict(inventory,
        records=records(inventory['records']),
        master_records=records(inventory['master_records']),
        rigs={key: dict(entry, entries=records(entry['entries'])) for key, entry in inventory['rigs'].items()})
    return _remap(projected, plan['rig'], clone)


def _clone(scene, plan, owned):
    import bpy
    from . import bone_collections
    rig = plan['rig']
    clone = rig.copy()
    owned['objects'].append(clone)
    clone.data = rig.data.copy()
    owned['data'].append(clone.data)
    clone.data.use_fake_user = False
    # Scene seeks are for body evaluation, not private UI/widget generation.
    # This flag belongs only to the owned data copy; its original stays exact.
    clone.data[bone_collections.AUTO_KEY] = 0
    scene.collection.objects.link(clone)
    clone.hide_render = True
    clone.animation_data_create()
    clone.animation_data.action = plan['action']
    clone.animation_data.action_slot = next(slot for slot in plan['action'].slots if slot.handle == plan['slot'])
    clone.animation_data.use_nla = False
    clone.animation_data.action_blend_type, clone.animation_data.action_influence = 'REPLACE', 1.
    settings = getattr(clone, 'wiggle', None)
    if settings is not None:
        settings.freeze = True
    for pb in clone.pose.bones:
        for constraint in tuple(pb.constraints):
            if pb.name not in plan['closure']:
                pb.constraints.remove(constraint)
                continue
            for prop in constraint.bl_rna.properties:
                if prop.type == 'POINTER' and not prop.is_readonly:
                    value = getattr(constraint, prop.identifier)
                    if value is rig:
                        setattr(constraint, prop.identifier, clone)
                    elif value is rig.data:
                        setattr(constraint, prop.identifier, clone.data)
    for curve in tuple(clone.animation_data.drivers):
        if (curve.data_path, curve.array_index) not in plan['drivers']:
            clone.animation_data.drivers.remove(curve)
            continue
        for variable in curve.driver.variables:
            for target in variable.targets:
                if target.id is rig:
                    target.id = clone
                elif target.id is rig.data:
                    target.id = clone.data
    return clone


def _frame(scene, frame):
    integer = math.floor(frame)
    scene.frame_set(integer, subframe=frame-integer)


def _pose(context, rig, names):
    evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
    return evaluated.matrix_world.copy(), {name: evaluated.pose.bones[name].matrix.copy() for name in names}


def _error(actual, expected):
    pairs = [(actual[0], expected[0])] + [(actual[1][name], expected[1][name]) for name in expected[1]]
    matrix, position, rotation, scale = 0., 0., 0., 0.
    for first, second in pairs:
        matrix = max(matrix, *(abs(float(first[i][j]-second[i][j])) for i in range(4) for j in range(4)))
        la, qa, sa = first.decompose()
        lb, qb, sb = second.decompose()
        position = max(position, (la-lb).length)
        rotation = max(rotation, qa.rotation_difference(qb).angle)
        scale = max(scale, max(abs(a-b) for a, b in zip(sa, sb)))
    _require(all(math.isfinite(value) for value in (matrix, position, rotation, scale)), 'Controller output is non-finite.')
    return dict(matrix=matrix, position=position, rotation=rotation, scale=scale)


def _acceptable(errors):
    return (errors['matrix'] <= MATRIX_TOLERANCE and errors['position'] <= 2.e-4
            and errors['rotation'] <= 2.e-3 and errors['scale'] <= 2.e-4)


def _quality_policy(context, quality):
    _require(type(quality) is str and quality in {'EDITABLE', 'STRICT'},
             'Choose EDITABLE or STRICT controller playback quality.')
    try:
        scale_length = context.scene.unit_settings.scale_length
    except (AttributeError, ReferenceError) as error:
        raise ValueError('Controller playback needs the original scene length units.') from error
    _require(type(scale_length) in (int, float) and math.isfinite(scale_length) and scale_length > 0.,
             'Controller playback needs finite positive scene length units.')
    return dict(quality=quality, scene_meters_per_unit=float(scale_length),
        comparison='world object and native body bones' if quality == 'EDITABLE' else 'legacy object-world and rig-space native bones',
        units=dict(position_meters='meters', rotation_radians='radians', scale_relative='unitless', shape_relative='unitless',
                   native_position='Blender units', native_absolute_scale='raw scale components'),
        matched_sample_gate=dict(matrix=MATRIX_TOLERANCE, position=2.e-4, rotation=2.e-3, scale=2.e-4),
        interval_policy='world playback budget only' if quality == 'EDITABLE' else 'historical native matrix and TRS gate',
        position_tolerance_meters=.001 if quality == 'EDITABLE' else None,
        rotation_tolerance_radians=.004363323129985824 if quality == 'EDITABLE' else 2.e-3,
        scale_relative_tolerance=2.e-4 if quality == 'EDITABLE' else None,
        shape_relative_tolerance=2.e-4 if quality == 'EDITABLE' else None,
        native_matrix_tolerance=MATRIX_TOLERANCE if quality == 'STRICT' else None,
        native_position_tolerance=2.e-4 if quality == 'STRICT' else None,
        native_absolute_scale_tolerance=2.e-4 if quality == 'STRICT' else None,
        scope='sampled object and native body bone matrices; no mesh-vertex or continuous-time guarantee')


def _unit_quaternion(value):
    result = tuple(float(component) for component in value)
    _require(len(result) == 4 and all(math.isfinite(component) for component in result),
             'World playback has a non-finite quaternion decomposition.')
    length = math.hypot(*result)
    _require(math.isfinite(length) and length > 0., 'World playback has a singular quaternion decomposition.')
    return tuple(component/length for component in result)


def _quaternion_angle(first, second):
    """Double-precision normalized shortest angle, invariant under q versus -q."""
    a, b = _unit_quaternion(first), _unit_quaternion(second)
    w = sum(x*y for x, y in zip(a, b))
    x = a[0]*b[1]-a[1]*b[0]-a[2]*b[3]+a[3]*b[2]
    y = a[0]*b[2]+a[1]*b[3]-a[2]*b[0]-a[3]*b[1]
    z = a[0]*b[3]-a[1]*b[2]+a[2]*b[1]-a[3]*b[0]
    return 2.*math.atan2(math.hypot(x, y, z), abs(w))


def _world_playback_error(actual, expected, meters_per_unit):
    """World TRS and rotation-free stretch, separate from raw matrix diagnostics."""
    def components(matrix):
        rows = tuple(tuple(float(matrix[i][j]) for j in range(4)) for i in range(4))
        _require(all(math.isfinite(value) for row in rows for value in row),
                 'World playback contains a non-finite matrix.')
        axes = tuple(math.hypot(*(rows[row][column] for row in range(3))) for column in range(3))
        volume = math.prod(axes)
        determinant = (rows[0][0]*(rows[1][1]*rows[2][2]-rows[1][2]*rows[2][1])
            - rows[0][1]*(rows[1][0]*rows[2][2]-rows[1][2]*rows[2][0])
            + rows[0][2]*(rows[1][0]*rows[2][1]-rows[1][1]*rows[2][0]))
        _require(math.isfinite(volume) and volume > 0. and abs(determinant) > volume*1.e-12,
                 'World playback has a singular or numerically degenerate body matrix.')
        location, quaternion, scale = matrix.decompose()
        location, scale = tuple(map(float, location)), tuple(map(float, scale))
        _require(len(location) == len(scale) == 3
                 and all(math.isfinite(value) for value in (*location, *scale)) and all(value != 0. for value in scale),
                 'World playback has a singular or non-finite matrix decomposition.')
        w, x, y, z = _unit_quaternion(quaternion)
        rotation = ((1.-2.*(y*y+z*z), 2.*(x*y-z*w), 2.*(x*z+y*w)),
                    (2.*(x*y+z*w), 1.-2.*(x*x+z*z), 2.*(y*z-x*w)),
                    (2.*(x*z-y*w), 2.*(y*z+x*w), 1.-2.*(x*x+y*y)))
        stretch = tuple(tuple(sum(rotation[k][i]*rows[k][j] for k in range(3))
                              for j in range(3)) for i in range(3))
        return location, (w, x, y, z), scale, stretch, rows

    pairs = [(actual[0], expected[0])] + [(actual[0] @ actual[1][name], expected[0] @ expected[1][name])
                                         for name in expected[1]]
    worst = dict(world_matrix=0., position_meters=0., rotation_radians=0., scale_relative=0., shape_relative=0.)
    for first, second in pairs:
        la, qa, sa, stretch_a, rows_a = components(first)
        lb, qb, sb, stretch_b, rows_b = components(second)
        worst['world_matrix'] = max(worst['world_matrix'],
                                    *(abs(rows_a[i][j]-rows_b[i][j]) for i in range(4) for j in range(4)))
        worst['position_meters'] = max(worst['position_meters'], math.dist(la, lb)*meters_per_unit)
        worst['rotation_radians'] = max(worst['rotation_radians'], _quaternion_angle(qa, qb))
        worst['scale_relative'] = max(worst['scale_relative'], *(abs(a-b)/abs(b) for a, b in zip(sa, sb)))
        worst['shape_relative'] = max(worst['shape_relative'],
            *(abs(stretch_a[i][j]-stretch_b[i][j])/abs(sb[j]) for i in range(3) for j in range(3)))
    _require(all(math.isfinite(value) for value in worst.values()), 'World playback error is non-finite.')
    return worst


def _playback_acceptable(errors, policy):
    if policy['quality'] == 'STRICT':
        return _acceptable(errors)
    return (errors['position_meters'] <= policy['position_tolerance_meters']
            and errors['rotation_radians'] <= policy['rotation_tolerance_radians']
            and errors['scale_relative'] <= policy['scale_relative_tolerance']
            and errors['shape_relative'] <= policy['shape_relative_tolerance'])


def _match_limb(context, solver, inventory, entry, wanted, expected_world, raw_names, work):
    """Escalate search only when actual evaluated motion misses the same gate."""
    from . import limb_ik_fk as match
    before = _raw(solver, raw_names)
    affected = set()

    def acceptable():
        work['limb_checks'] += 1
        return _acceptable(_error(_pose(context, solver, wanted), (expected_world, wanted)))

    work['ordinary'] += 1
    affected.update(match._match_ik(context, solver, inventory, entry, wanted))
    affected.update(match._match_toe(context, solver, entry, wanted))
    if acceptable():
        work['ordinary_accepted'] += 1
        return affected

    # Reproduce the original precise entry state, rather than refining a
    # different Pole layout left by the ordinary attempt. Earlier limbs stay.
    _restore_raw(solver, before)
    context.view_layer.update()
    work['precise'] += 1
    affected.update(match._match_ik(context, solver, inventory, entry, wanted, precise=True))
    affected.update(match._match_toe(context, solver, entry, wanted))
    if acceptable():
        work['precise_accepted'] += 1
        return affected

    work['refine'] += 1
    match._refine_calibrated_reach(context, solver, inventory, entry, wanted)
    affected.update(match._match_toe(context, solver, entry, wanted))
    if acceptable():
        work['refine_accepted'] += 1
    # Local checks choose work, not publication. The complete-body gate below
    # also catches dependencies between separate limb solves.
    return affected


def _match_body(context, solver, inventory, desired, source_raw, raw_names, work):
    """Keep one original full-precision retry for cross-limb interactions."""
    from . import limb_ik_fk as match
    affected = set()
    for key in sorted(inventory['rigs']):
        entry = inventory['rigs'][key]
        wanted = {name: desired[1][name] for name in match.pose_names(entry)}
        affected.update(_match_limb(context, solver, inventory, entry, wanted, desired[0], raw_names, work))
    errors = _error(_pose(context, solver, desired[1]), desired)
    work['full_body_checks'] += 1
    if _acceptable(errors):
        return affected, errors

    # The staged local route may choose different, still locally acceptable
    # controls. A coupled body gets exactly one old b02 precise/refine route
    # from the same source input snapshot before the unchanged final refusal.
    work['full_fallback'] += 1
    _restore_raw(solver, source_raw)
    context.view_layer.update()
    affected = set()
    for key in sorted(inventory['rigs']):
        entry = inventory['rigs'][key]
        wanted = {name: desired[1][name] for name in match.pose_names(entry)}
        work['precise'] += 1
        affected.update(match._match_ik(context, solver, inventory, entry, wanted, precise=True))
        work['refine'] += 1
        match._refine_calibrated_reach(context, solver, inventory, entry, wanted)
        affected.update(match._match_toe(context, solver, entry, wanted))
    errors = _error(_pose(context, solver, desired[1]), desired)
    work['full_body_checks'] += 1
    if _acceptable(errors):
        work['full_fallback_accepted'] += 1
    return affected, errors


def _stabilize_pole_phase(context, solver, inventory, desired, references):
    """Keep each limb's first native angle and move its Pole to compensate.

    Native pole_angle is bounded to +/-pi. Interpolating equivalent angles on
    opposite sides of that boundary rotates the limb through the wrong plane.
    These references belong to this build and survive its adaptive passes.
    """
    from . import limb_ik_fk as match
    affected = set()
    for key in sorted(inventory['rigs']):
        entry = inventory['rigs'][key]
        constraint = match._fixed_pole_constraint(entry)
        angle = float(constraint.pole_angle)
        _require(math.isfinite(angle), 'A solved IK Pole angle is non-finite.')
        if key not in references:
            animation = solver.animation_data
            drivers = animation.drivers if animation is not None else ()
            path = constraint.path_from_id('pole_angle')
            _require(not any(curve.data_path == path and not curve.mute
                             for curve in drivers),
                     'An active IK Pole angle driver owns this limb; controller conversion kept the original motion.')
        reference = references.setdefault(key, angle)
        if angle != reference:
            constraint.pole_angle = reference
            match._update(context, solver)
            wanted = {name: desired[1][name] for name in match.pose_names(entry)}
            match._match_pole_plane(context, solver, entry, wanted, precise=True)
            affected.update(match._match_toe(context, solver, entry, wanted))
        affected.add(entry['pole'].name)
    return affected


def _capture_channels(solver, inventory, affected, frame, result, previous):
    from mathutils import Euler, Quaternion
    for name in sorted(affected):
        pb = solver.pose.bones[name]
        rotation_path = 'rotation_quaternion' if pb.rotation_mode == 'QUATERNION' else 'rotation_euler'
        _require(pb.rotation_mode == 'QUATERNION' or pb.rotation_mode in {'XYZ','XZY','YXZ','YZX','ZXY','ZYX'},
                 'Controller motion currently supports Euler or quaternion controls.')
        rotation = pb.rotation_quaternion.copy() if rotation_path == 'rotation_quaternion' else pb.rotation_euler.copy()
        if rotation_path == 'rotation_quaternion':
            _require(rotation.magnitude > 1.e-8, 'A solved controller has a zero quaternion.')
            rotation.normalize()
            if name in previous and rotation.dot(previous[name]) < 0:
                rotation.negate()
        elif name in previous:
            rotation = rotation.to_quaternion().to_euler(pb.rotation_mode, previous[name])
        previous[name] = rotation.copy()
        fields = (('location', pb.location), (rotation_path, rotation), ('scale', pb.scale))
        for field, values in fields:
            for index, value in enumerate(values):
                _require(math.isfinite(value), 'A solved controller channel is non-finite.')
                result.setdefault((pb.path_from_id(field), index), []).append(float(value))
    for entry in inventory['rigs'].values():
        target = solver.pose.bones[entry['target'].name]
        from .limb_ik_fk import property_path
        result.setdefault((property_path(target), 0), []).append(1.)
        for pb, constraint, record in entry['entries']:
            if record['role'] == 'IK':
                result.setdefault((constraint.path_from_id('pole_angle'), 0), []).append(float(constraint.pole_angle))


def _write_channels(action, handle, result, retained_indices=None):
    from . import control_pose_offset as offset
    bag = offset._bag(action, handle)
    channels = result['channels']
    for curve in tuple(bag.fcurves):
        if (curve.data_path, curve.array_index) in channels:
            bag.fcurves.remove(curve)
    for (path, index), values in channels.items():
        selected = retained_indices.get((path, index), tuple(range(len(values)))) if retained_indices else tuple(range(len(values)))
        curve = bag.fcurves.new(path, index=index)
        # Its native update callback merges nearby keys; configure it while empty.
        curve.extrapolation = 'CONSTANT'
        curve.keyframe_points.add(len(selected))
        constant = all(value == values[0] for value in values)
        for point, item in zip(curve.keyframe_points, selected):
            point.co = result['frame_times'][item], values[item]
            point.interpolation = 'CONSTANT' if constant and path.endswith('["ik_fk"]') else 'LINEAR'
        # Times are already ordered. update() would merge distinct adaptive samples.


def _probes(times):
    return tuple(sorted(set(times) | {a+(b-a)*fraction for a, b in zip(times, times[1:])
                                    for fraction in (.25, .5, .75)}))


def _verify(context, scene, source, playback, raw, plan, times):
    editable = plan['playback_policy']['quality'] == 'EDITABLE'
    fields = ('matrix', 'position', 'rotation', 'scale')
    if editable:
        fields += ('world_matrix', 'position_meters', 'rotation_radians', 'scale_relative', 'shape_relative')
        fields += ('sample_matrix', 'sample_position', 'sample_rotation', 'sample_scale')
    worst = {key: 0. for key in fields}
    samples = set(times)
    failed = []
    for frame in _probes(times):
        _restore_raw(source, raw)
        _restore_raw(playback, raw)
        _frame(scene, frame)
        context.view_layer.update()
        actual, expected = _pose(context, playback, plan['native_names']), _pose(context, source, plan['native_names'])
        errors = _error(actual, expected)
        if editable:
            errors.update(_world_playback_error(actual, expected, plan['playback_policy']['scene_meters_per_unit']))
        for key, value in errors.items():
            worst[key] = max(worst[key], value)
        accepted = _playback_acceptable(errors, plan['playback_policy'])
        if editable and frame in samples:
            for key in ('matrix', 'position', 'rotation', 'scale'):
                worst['sample_'+key] = max(worst['sample_'+key], errors[key])
            accepted = accepted and _acceptable(errors)
        if not accepted:
            failed.append(frame)
    return worst, tuple(failed)


def build_controller_channels(context, rig, action, slot_handle=None, *, frame_start=None, frame_end=None, sample_step=1.0,
                              quality='EDITABLE'):
    """Return validated IK overlay channels; never bind or modify an author Action.

    Caller-owned import copies can use apply_to_action after their immutable
    Source baseline has been fixed.  Actual new-Action playback is verified,
    including authored subframes and interval quarter probes.
    """
    _require_private_evaluation_idle()
    import bpy
    from . import forearm_twist, controller_animation_curves
    offset, api, match, limb, inputs = _api()
    plan = _source_plan(context, rig, action, slot_handle, frame_start, frame_end, sample_step, quality=quality)
    if plan['conversion_state'] == 'NATIVE_FK':
        return plan
    source_proof = plan['source_hash']
    raw = _raw(rig, plan['closure'])
    missing_inputs = _required_inputs(plan)
    owned = dict(objects=[], data=[], actions=[])
    scene = None
    times = plan['frame_times']
    work = dict(ordinary=0, precise=0, refine=0, full_fallback=0,
                ordinary_accepted=0, precise_accepted=0, refine_accepted=0,
                full_fallback_accepted=0, limb_checks=0, full_body_checks=0,
                sample_solves=0, adaptive_passes=0,
                cumulative_includes_adaptive_recomputations=True,
                units='helper invocations; refine counts include calls that return early, not inner candidates or time')
    try:
        scene = bpy.data.scenes.new('Controller Motion Evaluation')
        scene.render.fps, scene.render.fps_base = context.scene.render.fps, context.scene.render.fps_base
        settings = getattr(scene, 'wiggle', None)
        if settings is not None:
            settings.enable = False
        source, solver, playback = [_clone(scene, plan, owned) for _ in range(3)]
        solver.animation_data.action = None
        playback.animation_data.action = None
        inventory = _solver_inventory(plan, solver)
        pole_references = {}
        with forearm_twist.defer_runtime(context, flush_on_exit=False):
            with context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=solver, active_object=solver):
                for refinement in range(MAX_REFINEMENTS+1):
                    work['adaptive_passes'] += 1
                    _require(len(times) <= MAX_SAMPLES and len(times)*len(plan['native_names']) <= MAX_BONE_SAMPLES,
                             'Controller accuracy needs more samples than this bounded operation supports.')
                    channels, filled, previous = {}, {}, {}
                    solve_worst = 0.
                    for frame in times:
                        _restore_raw(source, raw)
                        _frame(scene, frame)
                        context.view_layer.update()
                        desired = _pose(context, source, plan['native_names'])
                        source_evaluated = source.evaluated_get(context.evaluated_depsgraph_get())
                        source_raw = _raw(source_evaluated, plan['closure'])
                        _capture_inputs(source_evaluated, missing_inputs, filled)
                        _restore_raw(solver, source_raw)
                        context.view_layer.update()
                        work['sample_solves'] += 1
                        affected, errors = _match_body(context, solver, inventory, desired, source_raw, plan['closure'], work)
                        affected.update(_stabilize_pole_phase(context, solver, inventory, desired, pole_references))
                        errors = _error(_pose(context, solver, plan['native_names']), desired)
                        work['full_body_checks'] += 1
                        solve_worst = max(solve_worst, errors['matrix'])
                        _require(_acceptable(errors),
                                 f'Installed IK cannot reproduce body frame {frame:g} within tolerance '
                                 f'(matrix {errors["matrix"]:.6g}, position {errors["position"]:.6g}, '
                                 f'rotation {errors["rotation"]:.6g}, scale {errors["scale"]:.6g}); original motion was kept.')
                        _capture_channels(solver, inventory, affected, frame, channels, previous)
                    filled_keys = tuple(key for key in filled if key not in channels)
                    channels.update((key, filled[key]) for key in filled_keys)
                    result = dict(conversion_state='CONTROLLER', source_action=action, source_slot=plan['slot'],
                                  channels=channels, frame_times=times, native_names=plan['native_names'],
                                  quality=quality, playback_policy=dict(plan['playback_policy']))
                    candidate = action.copy()
                    owned['actions'].append(candidate)
                    candidate.use_fake_user = False
                    _write_channels(candidate, plan['slot'], result)
                    playback.animation_data.action = candidate
                    playback.animation_data.action_slot = next(slot for slot in candidate.slots if slot.handle == plan['slot'])
                    full_error, failed = _verify(context, scene, source, playback, raw, plan, times)
                    if not failed:
                        break
                    _require(refinement < MAX_REFINEMENTS,
                             'Controller interpolation cannot reproduce the original subframe motion; original motion was kept.')
                    playback.animation_data.action = None
                    owned['actions'].remove(candidate)
                    bpy.data.actions.remove(candidate)
                    times = tuple(sorted(set(times) | set(failed)))
                compressed = controller_animation_curves.compress(channels, times)
                reduced = action.copy()
                owned['actions'].append(reduced)
                reduced.use_fake_user = False
                _write_channels(reduced, plan['slot'], result, compressed['indices'])
                playback.animation_data.action = reduced
                playback.animation_data.action_slot = next(slot for slot in reduced.slots if slot.handle == plan['slot'])
                reduced_error, reduced_failed = _verify(context, scene, source, playback, raw, plan, times)
                result['retained_indices'] = None if reduced_failed else compressed['indices']
                result['diagnostics'] = dict(solve_matrix_error=solve_worst, full_error=full_error,
                    reduced_error=reduced_error, compression_accepted=not reduced_failed,
                    compression_fallback='Reduced body playback exceeded tolerance.' if reduced_failed else '',
                    before_points=sum(len(values) for values in channels.values()),
                    after_points=sum(len(compressed['indices'][key]) for key in channels) if not reduced_failed else sum(len(v) for v in channels.values()),
                    sample_count=len(times), probe_count=len(_probes(times)), refinement_passes=refinement,
                    isolated_secondary_bones=len(plan['secondary']), filled_input_channels=len(filled_keys))
                result['diagnostics']['solve_work'] = dict(work)
                result['filled_input_channels'] = filled_keys
        result['metadata'] = dict(schema=SCHEMA, frame_start=times[0], frame_end=times[-1],
            limbs=[kind+'.'+side for kind, side in sorted(plan['inventory']['rigs'])],
            authority='complete body IK; existing native FK orientation curves retained',
            native_input_sampling='evaluated raw RNA before constraints; solver Action detached',
            source_evaluation='explicit Action/slot; NLA excluded; captured raw defaults',
            pole_phase='per-limb first solved native angle retained across samples and adaptive passes; Pole position compensates; full-body native gate retained',
            source_action=action.name_full, source_slot=plan['slot'], source_fingerprint=source_proof,
            sample_count=len(times), body_bone_count=len(plan['native_names']),
            body_bones_sha256=_digest(plan['native_names']),
            filled_inputs=dict(count=len(filled_keys), channels_sha256=_digest(filled_keys),
                               sampling='raw RNA transforms and numeric controls before constraints'),
            verification='written Action strict native gate at every matched sample including removed keys; interval quarters use selected playback quality; no continuous-time guarantee',
            quality=quality, playback_budget=dict(plan['playback_policy']),
            world_playback_metrics='computed for EDITABLE' if quality == 'EDITABLE' else 'not computed; STRICT retains the historical replay contract',
            matrix_tolerance=MATRIX_TOLERANCE, matrix_tolerance_scope='sample solve and all written-Action matched samples; STRICT also all interval probes',
            error_metrics=dict(matrix='raw object-world and native bone-pose component differences; diagnostic only for EDITABLE interval probes',
                position='legacy raw object/bone-pose distance in Blender units', rotation='legacy raw quaternion angle',
                scale='legacy raw absolute scale difference', world_matrix='raw world-matrix component difference; diagnostic for EDITABLE',
                position_meters='world object and body bone positions times original scene scale_length',
                rotation_radians='world double-normalized quaternion shortest angle', scale_relative='world scale difference/reference axis scale',
                shape_relative='difference of each rotation-free R-transpose times world 3x3 stretch/reference axis scale',
                sample_prefix='legacy native errors at every written-Action matched sample, including samples omitted by key reduction'),
            diagnostics=result['diagnostics'], export_base_recapture_required=True)
        _require(len(_json(result['metadata'])) <= MAX_RECORD, 'Controller metadata exceeds its bounded record size.')
        _require(offset._action_hash(api, action, plan['slot']) == source_proof,
                 'The source animation changed during controller conversion; no result was published.')
        _require(_quality_policy(context, quality) == plan['playback_policy'],
                 'The original scene length units changed during controller conversion; no result was published.')
        return result
    finally:
        for obj in reversed(owned['objects']):
            bpy.data.objects.remove(obj, do_unlink=True)
        for block in reversed(owned['data']):
            _require(block.users == 0, 'An owned controller copy gained an unexpected data user; retained for recovery.')
            bpy.data.armatures.remove(block)
        for block in reversed(owned['actions']):
            _require(block.users == 0, 'An owned controller Action gained an unexpected user; retained for recovery.')
            bpy.data.actions.remove(block)
        if scene is not None:
            bpy.data.scenes.remove(scene)


def apply_to_action(action, slot_handle, built):
    """Populate an unbound caller-owned copy; caller discards it on write failure.

    Never pass an existing Custom/Source.  Imports already own the enclosing
    attachment/rollback transaction.  Non-output curves/slots/metadata survive.
    """
    _require(isinstance(built, dict) and built.get('conversion_state') in {'CONTROLLER', 'NATIVE_FK'},
             'Invalid controller conversion result.')
    if built['conversion_state'] == 'NATIVE_FK':
        _require(action is not built['source_action'] and not action.library and not action.asset_data
                 and action.users-int(action.use_fake_user) == 0,
                 'Native output must be an unbound independent Action copy.')
        action.pop(RECORD, None)
        return dict(action=action, conversion_state='NATIVE_FK', metadata=None, diagnostics=built['diagnostics'])
    _require(action is not built['source_action'] and not action.library and not action.asset_data
             and action.users-int(action.use_fake_user) == 0,
             'Controller output must be an unbound independent Action copy.')
    _require(slot_handle == built['source_slot'] and built.get('metadata', {}).get('schema') == SCHEMA,
             'Controller output Action slot or metadata is incompatible.')
    _require(built['channels'] and all(len(values) == len(built['frame_times']) for values in built['channels'].values()),
             'Controller output channels are incomplete.')
    _write_channels(action, slot_handle, built, built.get('retained_indices'))
    from . import animation_export_inputs as inputs
    action.pop(inputs.RECORD, None)
    action.pop(inputs.COUNT, None)
    action[RECORD] = _json(built['metadata'])
    return dict(action=action, conversion_state='CONTROLLER', metadata=built['metadata'], diagnostics=built['diagnostics'])


def _capture_display_checkpoint(rig):
    """Same-operation native references; never a reusable scene checkpoint."""
    from . import bone_collections
    rows = []
    for collection in rig.data.collections_all:
        members = tuple(collection.bones)
        rows.append(dict(ref=collection, pointer=collection.as_pointer(), name=collection.name,
                         parent=collection.parent,
                         parent_pointer=collection.parent.as_pointer() if collection.parent else None,
                         members=members, member_pointers=tuple(bone.as_pointer() for bone in members)))
    key = rig.as_pointer()
    return dict(data=rig.data, data_pointer=rig.data.as_pointer(), collections=tuple(rows), cache_key=key,
                cache={name: (getattr(bone_collections, name), key in getattr(bone_collections, name),
                              getattr(bone_collections, name).get(key))
                       for name in ('_FRAME_CACHE', '_FRAME_OWNER_REJECTIONS')})


def _validate_display_checkpoint(rig, saved):
    """Refuse structural edits before generic recovery can remove owned IDs."""
    from . import bone_collections
    _require(rig.data == saved['data'] and rig.data.as_pointer() == saved['data_pointer']
             and rig.as_pointer() == saved['cache_key'],
             'Controller recovery found a different native character; recovery needs review.')
    current = tuple(rig.data.collections_all)
    _require(tuple(collection.as_pointer() for collection in current)
             == tuple(row['pointer'] for row in saved['collections']),
             'Controller recovery found changed native collection identity or order; recovery needs review.')
    for collection, row in zip(current, saved['collections']):
        parent = collection.parent
        _require(collection == row['ref'] and row['ref'].as_pointer() == row['pointer']
                 and collection.name == row['name'] and parent == row['parent']
                 and (parent.as_pointer() if parent else None) == row['parent_pointer'],
                 'Controller recovery found changed native collection structure; recovery needs review.')
    _require(all(getattr(bone_collections, name) is table for name, (table, _present, _value) in saved['cache'].items()),
             'Controller display cache ownership changed; recovery needs review.')


def _restore_display_checkpoint(rig, saved):
    """Repair order only after the existing recovery restored every member set."""
    _validate_display_checkpoint(rig, saved)
    changed = []
    for row in saved['collections']:
        current = tuple(row['ref'].bones)
        pointers = tuple(bone.as_pointer() for bone in current)
        _require(len(pointers) == len(row['member_pointers']) and set(pointers) == set(row['member_pointers'])
                 and tuple(bone.as_pointer() for bone in row['members']) == row['member_pointers'],
                 'Controller recovery did not restore the native collection member set; recovery needs review.')
        if pointers != row['member_pointers']:
            changed.append((row, current))
    # All sets are proved before the first mutation. Never create collections,
    # add an absent member, or remove an unexpected member as a guessed repair.
    for row, current in changed:
        for bone in current:
            row['ref'].unassign(bone)
        for bone in row['members']:
            row['ref'].assign(bone)
    _validate_display_checkpoint(rig, saved)
    _require(all(tuple(bone.as_pointer() for bone in row['ref'].bones) == row['member_pointers']
                 for row in saved['collections']),
             'Controller recovery could not restore native collection member order; recovery needs review.')
    for table, present, value in saved['cache'].values():
        if present:
            table[saved['cache_key']] = value
        else:
            table.pop(saved['cache_key'], None)


def prepare(context, rig=None, *, frame_start=None, frame_end=None, quality='EDITABLE'):
    """Read-only current-Custom admission; no hidden Source-to-Custom switch."""
    _require_private_evaluation_idle()
    _quality_policy(context, quality)
    offset, api, match, limb, inputs = _api()
    item = api.worklist.current(context)
    _require(item is not None and item.side == 'CUSTOM', 'Select Custom to convert.')
    rig = item.rig if rig is None else rig
    variant = offset.prepare_variant(context, rig, label='Controller Animation')
    source = _source_plan(context, rig, variant['original'], variant['slot'], frame_start, frame_end, 1., quality=quality)
    _require(source['conversion_state'] == 'CONTROLLER', 'Install body limb controls first; native FK was kept.')
    pending = _pending_keyed_inputs(source, variant['frame'])
    _require(not pending, 'Unkeyed changes on ' + ', '.join(pending[:5])
             + (' and other inputs' if len(pending) > 5 else '')
             + '. Insert keyframes for these inputs before converting; the current pose was kept.')
    return dict(phase='prepared', variant=variant, source=source, quality=quality,
                display_checkpoint=_capture_display_checkpoint(rig))


def _rollback(plan):
    from . import control_pose_offset as offset
    try:
        staged = plan['variant'].get('staged_action') is not None
        if staged:
            _validate_display_checkpoint(plan['variant']['rig'], plan['display_checkpoint'])
        offset.rollback_variant(plan['variant'])
        if staged:
            _restore_display_checkpoint(plan['variant']['rig'], plan['display_checkpoint'])
    except Exception:
        plan['phase'] = 'recovery_required'
        raise
    plan['phase'] = 'discarded'


def build(plan):
    _require(isinstance(plan, dict) and plan.get('phase') == 'prepared', 'Controller conversion plan is no longer prepared.')
    offset, api, match, limb, inputs = _api()
    offset._variant_owner(plan['variant'], final=True)
    source = plan['source']
    _require(_quality_policy(source['context'], source['quality']) == source['playback_policy'],
             'The original scene length units changed after preparing controller conversion.')
    result = build_controller_channels(source['context'], source['rig'], source['action'], source['slot'],
                                       quality=source['quality'])
    action = offset.new_variant_action(plan['variant'])
    plan['phase'] = 'built'
    try:
        apply_to_action(action, source['slot'], result)
    except Exception:
        _rollback(plan)
        raise
    plan['result'] = result
    return dict(action=action, result=result)


def publish(plan, built, *, activate=True):
    _require(plan.get('phase') == 'built' and built['action'] is plan['variant'].get('staged_action')
             and built['result'] is plan.get('result'), 'Only the owned controller result can be published.')
    _require(_quality_policy(plan['source']['context'], plan['source']['quality']) == plan['source']['playback_policy'],
             'The original scene length units changed after building controller conversion; discard this result and convert again.')
    from . import control_pose_offset as offset, bone_collections, limb_ik, limb_ik_fk
    rig, context = plan['variant']['rig'], plan['variant']['context']
    before_modes = {key: limb_ik_fk.mode_for_rig(rig, entry)
                    for key, entry in plan['source']['inventory']['rigs'].items()}
    try:
        result = offset.publish_variant(plan['variant'], built['action'], activate=activate, preserve_context=True)
        if activate:
            context.view_layer.update()
            inventory = limb_ik._validate_inventory(rig)
            after_modes = {key: limb_ik_fk.mode_for_rig(rig, entry) for key, entry in inventory['rigs'].items()}
            _require(set(after_modes) == set(before_modes) and all(mode == 'IK' for mode in after_modes.values()),
                     'The new controller Action did not evaluate to IK at the current frame.')
            bone_collections.sync_limb_display(rig, inventory, force_flags=after_modes != before_modes)
            context.view_layer.update()
        plan['phase'] = 'published'
        plan['variant']['saved'].status = 'Controller Animation created as a new editable Custom version.'
        result.update(conversion_state='CONTROLLER', metadata=built['result']['metadata'], diagnostics=built['result']['diagnostics'])
        return result
    except Exception:
        _rollback(plan)
        raise


def discard(plan, built=None):
    if isinstance(plan, dict) and plan.get('phase') not in {'discarded', 'recovery_required'}:
        _rollback(plan)


def convert_current_action(context, rig=None, *, frame_start=None, frame_end=None, activate=True, quality='EDITABLE'):
    plan = prepare(context, rig, frame_start=frame_start, frame_end=frame_end, quality=quality)
    try:
        return publish(plan, build(plan), activate=activate)
    except Exception:
        if plan.get('phase') not in {'discarded', 'published', 'recovery_required'}:
            discard(plan)
        raise


def convert(context, rig=None, *, activate=True, quality='EDITABLE'):
    return convert_current_action(context, rig, activate=activate, quality=quality)
