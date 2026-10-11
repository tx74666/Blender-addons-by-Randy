"""Explicit native HoldLoop/Raise timing checks in the root's isolated copy.

Starts no process, saves no .blend and publishes nothing. The caller owns the
serial Blender window. author_hold_loop explicitly keys the test-owned Loop
before publication. run then only verifies that genuinely collected Loop and
the separately prepared/authored Raise; it never edits their curves.
"""
from contextlib import contextmanager
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import traceback
import uuid

SCHEMA = 'x.validation.character-composition-timing-fixture/1'
REPORT_SCHEMA = 'x.validation.character-composition-timing/1'
AUTHOR_SCHEMA = 'x.validation.character-composition-loop-authoring-fixture/1'
AUTHOR_FIELDS = {'schema', 'evidenceDirectory', 'holdLoopWorkItemId',
                 'expectedModelSha256', 'holdLoopRotationControl'}
FIELDS = {'schema', 'evidenceDirectory', 'holdLoopWorkItemId', 'raiseWorkItemId',
          'followingHoldWorkItemId', 'expectedModelSha256', 'poseTolerance',
          'inputTolerance', 'followingHoldPublication'}
PUBLICATION_FIELDS = {'manifestPath', 'manifestSha256', 'revision', 'fbxPath',
                      'fbxSha256', 'metadataPath', 'metadataSha256'}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_config(config):
    require(type(config) is dict and set(config) == FIELDS and config['schema'] == SCHEMA,
            'Supply the exact timing fixture schema and real row IDs.')
    ids = [uuid.UUID(config[field]).hex for field in
           ('holdLoopWorkItemId', 'raiseWorkItemId', 'followingHoldWorkItemId')]
    require(ids[0] == ids[2] and ids[1] != ids[0] and all(int(value, 16) != 0 for value in ids),
            'Raise must be distinct and follow the exact third, genuinely collected HoldLoop row.')
    require(type(config['expectedModelSha256']) is str
            and re.fullmatch('[0-9a-f]{64}', config['expectedModelSha256']), 'Pin the current prepared model SHA.')
    for name, upper in (('poseTolerance', 2e-4), ('inputTolerance', 1e-5)):
        require(type(config[name]) in (int, float) and math.isfinite(config[name])
                and 0 < config[name] <= upper, 'Unsupported numeric timing tolerance: ' + name)
    publication = config['followingHoldPublication']
    require(type(publication) is dict and set(publication) == PUBLICATION_FIELDS
        and type(publication['revision']) is int and publication['revision'] > 0
        and all(type(publication[key]) is str and Path(publication[key]).is_absolute()
                for key in ('manifestPath', 'fbxPath', 'metadataPath'))
        and all(type(publication[key]) is str and re.fullmatch('[0-9a-f]{64}', publication[key])
                for key in ('manifestSha256', 'fbxSha256', 'metadataSha256')),
        'Pin the actual previously collected Hold publication; an unsynced draft is not a destination.')
    return config


def validate_author_config(config):
    require(type(config) is dict and set(config) == AUTHOR_FIELDS and config['schema'] == AUTHOR_SCHEMA,
            'Supply the exact pre-publication Loop authoring schema.')
    require(int(uuid.UUID(config['holdLoopWorkItemId']).hex, 16) != 0,
            'Choose the genuine prepared third HoldLoop WorkItem.')
    require(type(config['expectedModelSha256']) is str
            and re.fullmatch('[0-9a-f]{64}', config['expectedModelSha256']), 'Pin the current prepared model SHA.')
    require(config['holdLoopRotationControl'] is None or type(config['holdLoopRotationControl']) is str,
            'Use null or one exact declared FK input name, never a guessed alias.')
    return config


def _sealed_loop(spec):
    require(spec['timeMode'] == 'HoldLoop', 'The genuine third timing row must be a HoldLoop.')
    # Accommodate only float32 serialization of the parent-authored values.
    for name, expected in (('editSpeed', 1.25), ('editPhase', .19),
                           ('referenceSpeed', .8), ('referencePhase', .07)):
        require(type(spec[name]) in (int, float) and math.isfinite(spec[name])
                and abs(spec[name] - expected) <= 1e-7,
                'The sealed Loop clock differs from the real Prepare contract: ' + name)


def _sealed_clocks(loop, raise_spec, hold):
    _sealed_loop(loop)
    _sealed_loop(hold)
    _sealed_raise(raise_spec)


def _sealed_raise(raise_spec):
    require(raise_spec['timeMode'] == 'Raise', 'Choose the genuine post-Collect prepared Raise row.')
    for name, expected in (('editSpeed', 1.7), ('editPhase', .17),
                           ('referenceSpeed', .8), ('referencePhase', .07)):
        require(type(raise_spec[name]) in (int, float) and math.isfinite(raise_spec[name])
                and abs(raise_spec[name] - expected) <= 1e-7,
                'The sealed Raise clock differs from the real Prepare contract: ' + name)


def b_clock(spec, seconds, *, following=None):
    """Independent seconds-domain oracle; no native strip fields are used."""
    require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0,
            'Timing seeks use finite nonnegative C seconds.')
    duration = spec['sampleEnd'] - spec['sampleStart']
    require(duration > 0 and spec['editSpeed'] > 0 and spec['fps'] > 0, 'B has no positive own clock.')
    mode = spec['timeMode']
    if mode == 'HoldPose':
        return {'role': 'B', 'seconds': spec['sampleStart'], 'cycle': 0}
    advance = seconds * spec['editSpeed'] / duration + spec['editPhase']
    if mode == 'HoldLoop':
        return {'role': 'B', 'seconds': spec['sampleStart'] + (advance % 1.) * duration,
                'cycle': math.floor(advance)}
    require(mode == 'Raise' and following is not None, 'Raise requires the actual following Hold clock.')
    transition = duration * (1. - spec['editPhase']) / spec['editSpeed']
    require(transition > 0, 'The fixture must expose a positive Raise segment before Hold.')
    if seconds < transition:
        return {'role': 'B', 'seconds': spec['sampleStart'] + advance * duration, 'cycle': 0}
    hold = b_clock(following, seconds - transition)
    require(following['timeMode'] in {'HoldPose', 'HoldLoop'}, 'A following Raise is not a Hold destination.')
    return {**hold, 'role': 'HOLD'}


def a_clock(action_start, action_end, action_rate, spec, seconds):
    """Expected source-frame clock of A, with its own source sampling rate."""
    require(action_end > action_start and action_rate > 0 and math.isfinite(action_rate),
            'A requires its actual positive source frame range/rate.')
    duration = (action_end - action_start) / action_rate
    advance = seconds * spec['referenceSpeed'] / duration + spec['referencePhase']
    return action_start + (advance % 1.) * (action_end - action_start)


def _write(path, report):
    temporary = path.with_suffix('.tmp.json')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _modules():
    return {name: importlib.import_module('character_designer.' + name) for name in
        ('animation_composition', 'animation_composition_export', 'animation_worklist',
         'unity_animation', 'animation_link', 'animation_worklist_fingerprint',
         'animation_worklist_collection')}


def _row(context, work_id, modules, model_hash):
    backend, worklist = modules['animation_composition'], modules['animation_worklist']
    saved = worklist.state(context)
    matches = [row for row in saved.items if row.work_item_id
               and uuid.UUID(row.work_item_id).hex == uuid.UUID(work_id).hex]
    if not matches:
        ready = [row for row in saved.composition_catalog if row.ready and row.work_item_id
                 and uuid.UUID(row.work_item_id).hex == uuid.UUID(work_id).hex]
        require(len(ready) == 1, 'Refresh/add the exact genuine prepared timing row: ' + work_id)
        matches = [backend.add_work_item(context, ready[0].clip_key, activate_new=False)]
    require(len(matches) == 1, 'Timing WorkItem is unavailable or ambiguous: ' + work_id)
    row = matches[0]
    require(row.rig is saved.rig and row.model_sha256 == model_hash and backend.item_spec(row) is not None,
            'Timing row must belong to the exact Main Rig and current prepared model.')
    backend._packet_for_item(row)
    worklist._check_baseline(row)
    return row


def _input_values(context, rig, scope):
    context.view_layer.update()
    rig = rig.evaluated_get(context.evaluated_depsgraph_get())
    result = []
    for path, index in scope.channels:
        value = rig.path_resolve(path)
        value = value[index] if hasattr(value, '__len__') else value
        require(type(value) in (int, float) and math.isfinite(value), 'Nonfinite exact FK input: ' + path)
        result.append(float(value))
    return result


def _assert_moving(action, slot, scope, spec, backend):
    start, end = backend._frame_range(spec)
    values = [[float(curve.evaluate(start + fraction * (end - start))) for curve in backend._curves(action, slot)
               if (curve.data_path, curve.array_index) in set(scope.channels)]
              for fraction in (.03, .27, .51, .79, .97)]
    require(values and values[0] and all(math.isfinite(value) for row in values for value in row)
            and max(abs(value - first) for row in values[1:] for value, first in zip(row, values[0])) > 1e-4,
            'The real moving timing row still contains only a constant Hold expression.')


def _pose(context, rig, names):
    context.view_layer.update()
    evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
    result = {}
    for name in names:
        bone = evaluated.pose.bones[name]
        matrix = bone.parent.matrix.inverted() @ bone.matrix if bone.parent else bone.matrix
        values = [float(value) for row in matrix for value in row]
        require(all(math.isfinite(value) for value in values), 'Nonfinite native timing pose: ' + name)
        result[name] = values
    return result


def _max_error(left, right, names):
    return max((abs(a - b) for name in names for a, b in zip(left[name], right[name])), default=0.)


@contextmanager
def _open(context, row, modules):
    """Activate the actual current-model row, restoring its entire prior binding."""
    backend, worklist = modules['animation_composition'], modules['animation_worklist']
    require(not backend.active(row.rig), 'Close the previous composition before timing acceptance.')
    saved = worklist.state(context)
    previous = backend._prior(context, row.rig)
    old_index, old_status, old_error, old_side = saved.active_index, saved.status, saved.has_error, row.side
    try:
        worklist.activate(context, row.item_id, 'CUSTOM')
        modules['animation_link'].get_link(row.rig)
        backend.enter(context, backend.item_spec(row), row.item_id)
        yield backend.validate(context, row.rig)
    finally:
        if backend.active(row.rig):
            backend.exit(context, row.rig, writeback=False)
        backend._restore_prior(context, row.rig, previous)
        saved.active_index, saved.status, saved.has_error, row.side = old_index, old_status, old_error, old_side
        context.view_layer.update()


def _loop_visibility(rig, bone, pose_hidden):
    """Match native Pose visibility, including unassigned bones during Solo."""
    solo = rig.data.collections.is_solo_active
    require(type(pose_hidden) is bool and type(solo) is bool,
            'Native Pose/Collection visibility must expose exact boolean state.')
    memberships = []
    for collection in bone.bone.collections:
        visible = collection.is_visible_effectively
        require(type(visible) is bool, 'Native effective Collection visibility is unavailable.')
        memberships.append({'name': collection.name, 'visible_effectively': visible})
    return {'pose_hidden': pose_hidden, 'solo_active': solo, 'collections': memberships,
            'visible': not pose_hidden and ((not memberships and not solo)
                or any(entry['visible_effectively'] for entry in memberships))}


def _loop_rotation_state(bone, field):
    """Detach real native rotation evidence; invalid values remain JSON-safe."""
    values = tuple(getattr(bone, field))
    basis = bone.matrix_basis.copy()
    quaternion = tuple(basis.to_quaternion())
    matrix = tuple(value for row in basis for value in row)
    finite = all(type(value) in (int, float) and math.isfinite(value)
                 for value in values + matrix + quaternion)
    valid = (finite and len(values) == (3 if field == 'rotation_euler' else 4)
             and len(matrix) == 16 and len(quaternion) == 4
             and sum(value * value for value in quaternion) > 0.)
    def plain(sequence):
        return [float(value) if type(value) in (int, float) and math.isfinite(value)
                else str(value) for value in sequence]
    return {'field': field, 'values': plain(values), 'basis': plain(matrix),
            'quaternion': plain(quaternion), 'finite': finite, 'valid': valid}


def _loop_rotation_delta(before, after):
    require(before['valid'] and after['valid'], 'Native rotation evidence contains invalid values.')
    left, right = before['quaternion'], after['quaternion']
    divisor = math.sqrt(sum(value * value for value in left) * sum(value * value for value in right))
    require(math.isfinite(divisor) and divisor > 0., 'Native rotation normalization is not finite/positive.')
    cosine = abs(sum(a * b for a, b in zip(left, right))) / divisor
    require(math.isfinite(cosine), 'Native normalized rotation comparison is not finite.')
    delta = 2. * math.acos(min(1., max(0., cosine)))
    require(math.isfinite(delta), 'Native FK rotation delta is not finite.')
    return delta


def _loop_select(context, rig, name, session, tracks, backend, pose_hidden, diagnostic):
    """Reacquire and verify the native B input after a real mapped frame update."""
    bone = rig.pose.bones[name]
    diagnostic['visibility'] = _loop_visibility(rig, bone, pose_hidden(bone))
    area, region, space = context.area, context.region, context.space_data
    ad = rig.animation_data
    diagnostic.update(object_mode=rig.mode, context_mode=context.mode,
        area_type=area.type if area else None, region_type=region.type if region else None,
        space_type=space.type if space else None,
        active_object=context.view_layer.objects.active.name if context.view_layer.objects.active else None,
        pose_object=context.pose_object.name if context.pose_object else None,
        tweak_mode=ad.use_tweak_mode if ad else None,
        action=ad.action.name if ad and ad.action else None,
        action_slot=ad.action_slot_handle if ad else None)
    require(context.window and context.window.scene == session['scene']
        and context.scene == session['scene'] and context.view_layer.name == session['view_layer']
        and context.view_layer.objects.active == rig and context.pose_object == rig
        and rig.mode == 'POSE' and context.mode == 'POSE' and rig.select_get()
        and area and area.type == 'VIEW_3D' and region and region.type == 'WINDOW'
        and space and space.type == 'VIEW_3D', 'The actual native FK Pose/View3D context changed.')
    current = backend._owned_tracks(rig, session)['EDIT']
    diagnostic['edit_track'] = current.name
    require(current == tracks['EDIT'] and ad and ad.use_tweak_mode
        and session['edit_target'] == 'B' and session['solo'] is False
        and ad.nla_tracks.active == current and current.select and current.strips[0].select
        and ad.action == session['edit_action'] and ad.action_slot_handle == session['edit_slot']
        and current.strips[0].action == session['edit_action']
        and current.strips[0].action_slot_handle == session['edit_slot'],
        'The exact B Full Stack Tweak Action/slot/active strip changed.')
    require(diagnostic['visibility']['visible'] and not bone.bone.hide_select
        and not bone.lock_rotation[0], 'The exact chosen FK input is no longer visible/selectable/unlocked.')
    diagnostic['selected_before'] = [pb.name for pb in rig.pose.bones if backend._pose_selection_owner(pb).select]
    rig.data.bones.active = rig.data.bones[name]
    for pb in rig.pose.bones:
        backend._pose_selection_owner(pb).select = pb.name == name
    diagnostic['selected_after'] = [pb.name for pb in rig.pose.bones if backend._pose_selection_owner(pb).select]
    diagnostic['active_bone'] = rig.data.bones.active.name if rig.data.bones.active else None
    selected = context.selected_pose_bones
    diagnostic['selected_pose_bones'] = [pb.name for pb in selected] if selected is not None else None
    require(diagnostic['selected_after'] == [name] and rig.data.bones.active == bone.bone
        and context.active_pose_bone == bone and diagnostic['selected_pose_bones'] == [name]
        and tuple(selected) == (bone,), 'Native FK selection/active Bone readback differs from the exact input.')
    return bone


def _author_loop(context, row, modules, requested, *, diagnostics=None, checkpoint=None):
    """Three real FK Pose/RNA keys; the packet and sealed DTO are untouched."""
    import bpy
    from composition_session_acceptance import _pose_hidden, _view3d
    backend, ua = modules['animation_composition'], modules['unity_animation']
    diagnostics = diagnostics if diagnostics is not None else {}
    require(type(bpy.app.background) is bool, 'Native background state must be an exact boolean.')
    undo_requested = not bpy.app.background
    diagnostics.update(requested_control=requested, candidates=[], frames=[],
        actualundo_requested=undo_requested, pendingNativeOperator=False, checkpoint_stage='authoring_started')
    with _open(context, row, modules) as (session, _item, scope, tracks):
        before = backend._motion_hash(row.custom_action, row.custom_slot)
        packet_before, spec_before = session['packet_sha256'], json.dumps(session['spec'], sort_keys=True)
        protected_hash = backend._motion_hash(session['base_action'], session['base_slot'])
        rotation_controls = []
        for name in sorted(set(scope.input_by_native.values())):
            bone = row.rig.pose.bones[name]
            field = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                     'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
            path = bone.path_from_id() + '.' + field
            indices = {index for p, index in scope.channels if p == path}
            visibility = _loop_visibility(row.rig, bone, _pose_hidden(bone))
            eligible = (indices == set(range(3 if field == 'rotation_euler' else 4))
                        and visibility['visible'] and not bone.bone.hide_select and not bone.lock_rotation[0])
            diagnostics['candidates'].append({'control': name, 'field': field,
                'scope_indices': sorted(indices), 'visibility': visibility,
                'hide_select': bone.bone.hide_select, 'lock_rotation': list(bone.lock_rotation),
                'eligible': eligible})
            if eligible:
                rotation_controls.append((name, field))
        if requested:
            rotation_controls = [entry for entry in rotation_controls if entry[0] == requested]
        require(rotation_controls, 'No exact visible native FK input is available for the authored HoldLoop.')
        name, field = rotation_controls[0]
        diagnostics.update(control=name, field=field, native_sources=sorted(
            native for native, input_name in scope.input_by_native.items() if input_name == name))
        start, end = backend._frame_range(session['spec'])
        # Read the genuine input pose, never a sampled exported output/fixture.
        raw_start = start + .02 * (end - start)
        mapped_start = row.rig.animation_data.nla_tweak_strip_time_to_scene(raw_start)
        keys = []
        with _view3d(context, row.rig) as view_context:
            ua._set_frame(context.scene, mapped_start)
            view_context.view_layer.update()
            initial_frame = {'phase': 'initial_input', 'B_frame': raw_start, 'C_frame': mapped_start,
                'actualundo_requested': undo_requested, 'checkpoint_stage': 'native_selection_readback'}
            diagnostics['frames'].append(initial_frame)
            bone = _loop_select(view_context, row.rig, name, session, tracks, backend, _pose_hidden, initial_frame)
            initial = _loop_rotation_state(bone, field)
            diagnostics['initial_rotation'] = initial
            require(initial['valid'], 'The genuine native HoldLoop input rotation is not finite/valid.')
            original = tuple(initial['values'])
            for fraction in (0., .5, 1.):
                raw_frame = start + fraction * (end - start)
                scene_frame = row.rig.animation_data.nla_tweak_strip_time_to_scene(raw_frame)
                expected_frame = tracks['EDIT'].strips[0].frame_start + (raw_frame - start) / session['spec']['editSpeed']
                require(abs(scene_frame - expected_frame) <= 1e-5, 'Native Tweak key clock does not map to the actual B frame.')
                ua._set_frame(context.scene, scene_frame)
                view_context.view_layer.update()
                frame_state = {'phase': fraction, 'B_frame': raw_frame, 'C_frame': scene_frame,
                    'actualundo_requested': undo_requested, 'checkpoint_stage': 'native_selection_readback'}
                diagnostics['frames'].append(frame_state)
                bone = _loop_select(view_context, row.rig, name, session, tracks, backend, _pose_hidden, frame_state)
                setattr(bone, field, original)
                if fraction == .5:
                    operation = frame_state['operator'] = {'minimum_rotation_radians': 1e-7}
                    operation['before'] = _loop_rotation_state(bone, field)
                    require(operation['before']['valid'], 'Native pre-transform FK rotation is not finite/valid.')
                    operation['poll'] = bpy.ops.transform.rotate.poll()
                    require(operation['poll'] is True, 'The actual native HoldLoop rotation operator poll failed.')
                    operation.update(actualundo_requested=undo_requested, pendingNativeOperator=True,
                                     checkpoint_stage='before_native_rotate')
                    diagnostics.update(pendingNativeOperator=True, checkpoint_stage='before_native_rotate')
                    frame_state['checkpoint_stage'] = 'before_native_rotate'
                    if checkpoint is not None:
                        checkpoint()
                    try:
                        result = bpy.ops.transform.rotate('EXEC_DEFAULT', undo_requested, value=.073, orient_axis='X', orient_type='LOCAL')
                    except Exception:
                        operation['exception'] = traceback.format_exc()
                        raise
                    operation['result'] = sorted(result)
                    operation['after'] = _loop_rotation_state(row.rig.pose.bones[name], field)
                    require(result == {'FINISHED'},
                            'The actual native HoldLoop middle FK Pose rotation did not finish.')
                    operation['rotation_delta_radians'] = _loop_rotation_delta(operation['before'], operation['after'])
                    require(operation['rotation_delta_radians'] > 1e-7,
                            'The finished native HoldLoop FK rotation produced no actual rotation change.')
                    operation.update(pendingNativeOperator=False, checkpoint_stage='native_rotate_verified')
                    diagnostics.update(pendingNativeOperator=False, checkpoint_stage='native_rotate_verified')
                    frame_state['checkpoint_stage'] = 'native_rotate_verified'
                    if checkpoint is not None:
                        checkpoint()
                require(row.rig.pose.bones[name].keyframe_insert(data_path=field, frame=scene_frame, group=name),
                        'The actual native HoldLoop selected-slot key operation failed.')
                keys.append({'phase': fraction, 'B_frame': raw_frame, 'C_frame': scene_frame})
        backend.validate(context, row.rig)
        selected_rotation = [curve for curve in backend._curves(session['edit_action'], session['edit_slot'])
                             if curve.data_path == row.rig.pose.bones[name].path_from_id() + '.' + field]
        require(selected_rotation and all(any(abs(point.co[0] - (start + .5 * (end - start))) <= 1e-5
            for point in curve.keyframe_points) for curve in selected_rotation),
            'Native keying did not place the middle key at the exact B-local selected-slot frame.')
        _assert_moving(session['edit_action'], session['edit_slot'], scope, session['spec'], backend)
        require(backend.commit(context, row.rig), 'The explicitly authored HoldLoop did not commit its own B Custom.')
        after = backend._motion_hash(row.custom_action, row.custom_slot)
        require(after != before and backend._motion_hash(session['base_action'], session['base_slot']) == protected_hash
                and backend._packet_for_item(row)['_sha256'] == packet_before
                and json.dumps(backend.item_spec(row), sort_keys=True) == spec_before,
                'HoldLoop authoring changed protected A, packet or sealed DTO.')
        return {'native_FK_transform_and_keys': True, 'control': name, 'keys': keys,
                'before_sha256': before, 'after_sha256': after, 'packet_DTO_Source_A_unchanged': True}


def _raw_timing(inputs, spec):
    values, timing = inputs['effective_values'], inputs['timing']
    times = values['timesSeconds']
    duration = spec['sampleEnd'] - spec['sampleStart']
    require(times and abs(times[0] - spec['sampleStart']) <= 1e-12
        and abs(times[-1] - spec['sampleEnd']) <= 1e-12
        and all(spec['sampleStart'] <= value <= spec['sampleEnd'] for value in times)
        and abs((timing['frame_end'] - timing['frame_start']) / (timing['fps'] / timing['fps_base']) - duration) <= 1e-10
        and timing['sample_rate'] == spec['fps'],
        'Raw B export times/duration were retimed by preview speed/phase or an extra rest frame.')
    return {'sampleStart': times[0], 'sampleEnd': times[-1], 'duration': duration,
            'sample_count': len(times), 'sample_rate': timing['sample_rate'], 'phase_speed_not_baked': True}


def _reset_clone(clone, scope):
    for (path, index), value in scope.canonical_defaults.items():
        clone.path_resolve(path)[index] = value
    for name, key, value in scope.fixed_properties:
        clone.pose.bones[name][key] = value


def _isolated_samples(context, row, times, modules):
    """Sample actual canonical B at its raw local time, without C/A clocks."""
    import bpy
    backend, ua = modules['animation_composition'], modules['unity_animation']
    with _open(context, row, modules) as (session, _item, scope, _tracks):
        before = backend.item_fingerprint(context, row)
        with backend.isolated_sync(context, row.rig) as inputs:
            _raw_timing(inputs, session['spec'])
            result = []
            for seconds in times:
                ua._set_frame(bpy.context.scene, 1. + seconds * session['spec']['fps'])
                result.append({'seconds': seconds, 'pose': _pose(bpy.context, inputs['rig'], scope.native_names),
                               'inputs': _input_values(bpy.context, inputs['rig'], scope)})
        require(backend.item_fingerprint(context, row) == before, 'Raw B sampling changed authored/effective content.')
        return result


def _isolated_a_samples(context, row, seeks, modules):
    """Independent A native poses using readonly Action, exact slot, raw clock."""
    import bpy
    backend, ua = modules['animation_composition'], modules['unity_animation']
    with _open(context, row, modules) as (session, _item, scope, _tracks):
        require(backend._motion_hash(session['base_action'], session['base_slot']) == session['base_hash'],
                'The independent A comparator lost its exact authenticated Source Action.')
        names = set(scope.mapping.indices) - set(getattr(scope.plan, 'delegated_names', ()))
        lower = names - set(scope.native_names)
        with backend.isolated_sync(context, row.rig) as inputs:
            clone, ad = inputs['rig'], inputs['rig'].animation_data
            old_action, old_slot = ad.action, ad.action_slot
            try:
                ad.action = None
                _reset_clone(clone, scope)
                ad.action = session['base_action']
                ad.action_slot = backend._slot(session['base_action'], session['base_slot'])
                ad.use_nla = False
                start, end = map(float, session['base_action'].frame_range)
                rate = session['base_action']['unity_sample_rate']
                result = []
                for seconds in seeks:
                    frame = a_clock(start, end, rate, session['spec'], seconds)
                    ua._set_frame(bpy.context.scene, frame)
                    result.append({'C_seconds': seconds, 'A_raw_frame': frame,
                                   'pose': _pose(bpy.context, clone, lower)})
            finally:
                ad.action = None
                _reset_clone(clone, scope)
                ad.action, ad.action_slot, ad.use_nla = old_action, old_slot, False
                bpy.context.view_layer.update()
        require(backend._motion_hash(session['base_action'], session['base_slot']) == session['base_hash'],
                'Independent A pose sampling changed its protected Source.')
        return result


def _published_hold(context, hold, modules, config):
    """Actual local Hold must equal the pinned complete physical publication."""
    backend, exporter = modules['animation_composition'], modules['animation_composition_export']
    pin = config['followingHoldPublication']
    require(Path(hold.manifest_path).resolve() == Path(pin['manifestPath']).resolve()
        and _sha(hold.manifest_path) == pin['manifestSha256'], 'The actual collected Hold manifest bytes changed.')
    link = modules['animation_worklist_collection']._current_link(modules['animation_worklist'].state(context), hold, None)
    require(link.get('revision') == pin['revision'] > 0, 'The Hold has no matching actual published revision.')
    for link_path, link_hash, pin_path, pin_hash in (
        ('fbxFile', 'fbxSha256', 'fbxPath', 'fbxSha256'),
        ('metadataFile', 'metadataSha256', 'metadataPath', 'metadataSha256')):
        path = Path(pin[pin_path]).resolve()
        require(path.is_file() and path.stat().st_size > 0 and Path(link[link_path]).resolve() == path
                and link[link_hash] == pin[pin_hash] == _sha(path), 'The actual collected Hold physical file/hash changed.')
    metadata_file = Path(pin['metadataPath'])
    require(metadata_file.stat().st_size <= 16 * 1024 * 1024, 'Published Hold metadata is too large for this bounded native fixture.')
    published = json.loads(metadata_file.read_text(encoding='utf-8-sig'))
    spec = backend.item_spec(hold)
    exporter.validate_export_receipt(published, spec, published.get('composition_export'), published.get('effective_values'))
    with _open(context, hold, modules) as (session, _item, _scope, _tracks):
        proof_before = backend.item_fingerprint(context, hold)
        with backend.isolated_sync(context, hold.rig) as inputs:
            timing = _raw_timing(inputs, spec)
            measured = inputs['composition_export']
            require(inputs['effective_values'] == published['effective_values']
                and measured['effectiveChannelsSha256'] == published['composition_export']['effectiveChannelsSha256']
                and measured['effectiveChannels'] == published['composition_export']['effectiveChannels']
                and measured['referenceActionBeforeSha256'] == published['composition_export']['referenceActionBeforeSha256'],
                'The local Hold is an unsynced changed draft; its complete numeric inputs do not match the collected publication.')
            require(type(published.get('duration')) in (int, float)
                and abs(published['duration'] - timing['duration']) <= 1e-10,
                'The actual returned Hold clip has the wrong raw positive duration.')
        require(backend.item_fingerprint(context, hold) == proof_before, 'Published Hold verification edited its current native Action.')
    return {'revision': pin['revision'], 'manifest_sha256': pin['manifestSha256'],
        'fbx_sha256': pin['fbxSha256'], 'metadata_sha256': pin['metadataSha256'],
        'complete_numeric_payload_equal': True, 'no_unsynced_local_Hold_substitution': True,
        'raw_export_timing': timing,
        'unity_collect_admission': 'Parent verifies Unity collection before permitting Raise Prepare.'}


def _assert_strip_clocks(session, tracks, backend, following):
    spec = session['spec']
    start, end = backend._frame_range(spec)
    edit = tracks['EDIT'].strips[0]
    expected_start = 1. - spec['editPhase'] * (end - start) / spec['editSpeed']
    require(abs(edit.action_frame_start - start) <= 1e-5 and abs(edit.action_frame_end - end) <= 1e-5
            and abs(edit.scale - 1. / spec['editSpeed']) <= 1e-6
            and abs(edit.frame_start - expected_start) <= 1e-5,
            'B native strip lost its own sample range, positive speed or phase.')
    base = tracks['BASE'].strips[0]
    a_start, a_end = map(float, session['base_action'].frame_range)
    arate = session['base_action']['unity_sample_rate']
    speed = spec['referenceSpeed'] * arate / spec['fps']
    require(abs(base.scale - 1. / speed) <= 1e-6
            and abs(base.frame_start - (1. - spec['referencePhase'] * (a_end - a_start) / speed)) <= 1e-5,
            'A native strip used B source rate or dropped its own speed/phase.')
    result = {'B_fps': spec['fps'], 'B_speed': spec['editSpeed'], 'B_phase': spec['editPhase'],
              'A_source_rate': arate, 'A_speed': spec['referenceSpeed'], 'A_phase': spec['referencePhase']}
    if following:
        hold = following
        hs, he = backend._frame_range(hold)
        hspeed = hold['editSpeed'] * hold['fps'] / spec['fps']
        transition_frame = 1. + (spec['sampleEnd'] - spec['sampleStart']) * (1. - spec['editPhase']) / spec['editSpeed'] * spec['fps']
        role = 'HOLD_PHASE' if hold['timeMode'] == 'HoldLoop' and hold['editPhase'] % 1. else 'HOLD'
        first = tracks[role].strips[0]
        phase = hold['editPhase'] % 1. if role == 'HOLD_PHASE' else 0.
        require(abs(first.frame_start - transition_frame) <= 1e-5
            and abs(first.action_frame_start - (hs + phase * (he - hs))) <= 1e-5
            and abs(first.scale - 1. / hspeed) <= 1e-6 and edit.extrapolation == 'NOTHING',
            'Raise transition did not adopt the actual Hold\'s own start/phase/rate/speed.')
        result['following_Hold'] = {'fps': hold['fps'], 'speed': hold['editSpeed'],
            'phase': hold['editPhase'], 'mode': hold['timeMode'], 'transition_frame': transition_frame}
    return result


def _compare(context, row, seeks, expected, expected_a, modules, config, following=None):
    from composition_session_acceptance import _a_only
    backend, ua = modules['animation_composition'], modules['unity_animation']
    with _open(context, row, modules) as (session, _item, scope, tracks):
        if session['spec']['timeMode'] != 'HoldPose':
            _assert_moving(row.custom_action, row.custom_slot, scope, session['spec'], backend)
        proof = backend.item_fingerprint(context, row)
        require(proof.get('fingerprint_known'), 'The actual timing B fingerprint is unknown.')
        clocks = _assert_strip_clocks(session, tracks, backend, following)
        delegated = set(getattr(scope.plan, 'delegated_names', ()))
        names = set(scope.mapping.indices) - delegated
        lower = names - set(scope.native_names)
        require(lower and scope.native_names, 'Timing needs independent A lower and B upper domains.')
        sampled, upper_poses = [], []
        require(len(seeks) == len(expected) == len(expected_a), 'Every native seek needs independent A and B samples.')
        for seconds, reference, a_reference in zip(seeks, expected, expected_a):
            frame = 1. + seconds * session['spec']['fps']
            ua._set_frame(context.scene, frame)
            actual = _pose(context, row.rig, names)
            actual_inputs = _input_values(context, row.rig, scope)
            a_only = _a_only(context, session, tracks, names, frame, modules)
            upper_error = _max_error(actual, reference['pose'], scope.native_names)
            lower_error = _max_error(actual, a_only, lower)
            independent_a_error = _max_error(actual, a_reference['pose'], lower)
            require(len(actual_inputs) == len(reference['inputs']) == len(scope.channels),
                    'The actual own-clock input comparison omitted a required scoped channel.')
            input_error = max(abs(a - b) for a, b in zip(actual_inputs, reference['inputs']))
            require(max(upper_error, lower_error, independent_a_error) <= config['poseTolerance']
                    and input_error <= config['inputTolerance'],
                    'Own-clock native C/B/A mismatch: ' + str((seconds, upper_error, lower_error, independent_a_error, input_error)))
            require(backend.item_fingerprint(context, row) == proof, 'Seeking an actual timing view edited B.')
            clock = b_clock(session['spec'], seconds, following=following)
            require(abs(clock['seconds'] - reference['seconds']) <= 1e-10, 'Raw B sample was not selected by the independent own clock.')
            sampled.append({'C_seconds': seconds, 'C_frame': frame, 'expected_role': clock['role'],
                'B_local_seconds': clock['seconds'], 'B_cycle': clock['cycle'],
                'A_source_frame': a_clock(*map(float, session['base_action'].frame_range),
                    session['base_action']['unity_sample_rate'], session['spec'], seconds),
                'upper_vs_raw_B': upper_error, 'lower_vs_A_NLA': lower_error,
                'lower_vs_independent_raw_A': independent_a_error, 'scoped_input_error': input_error})
            upper_poses.append({name: actual[name] for name in scope.native_names})
        require(max(_max_error(upper_poses[0], pose, scope.native_names) for pose in upper_poses[1:]) > 1e-5,
                'Timing seeks did not exercise a genuinely varying native upper-body pose.')
        return {'mode': session['spec']['timeMode'], 'clocks': clocks, 'samples': sampled,
                'authored_B_unchanged_by_seeking': True, 'fingerprint': proof,
                'upper_native_count': len(scope.native_names), 'lower_native_count': len(lower),
                'delegated_native_count': len(delegated)}


def _numeric_proof(context, row, modules):
    """Use the physical helper's exact detached native-provenance API."""
    from composition_sync_acceptance import foreground_one
    result = foreground_one(context, row)
    require(result.get('schema') == 'x.validation.character-composition-b-numeric/1'
            and result.get('completed') is True, 'The official read-only single-B foreground proof did not finish.')
    # Native RNA/ID handles never escape into a proof consumed later.
    return json.loads(json.dumps(result, allow_nan=False))


def author_hold_loop(context, config):
    """Explicit native FK authoring BEFORE third SOURCE Loop Sync/Collect.

    Returns a detached numeric_proof for the physical run_one API. The caller
    must publish/collect this exact content before preparing the genuine Raise.
    """
    config = validate_author_config(config)
    directory = Path(config['evidenceDirectory']).resolve()
    require(directory.is_dir(), 'Create the dedicated isolated timing evidence directory first.')
    path = directory / 'composition_HoldLoop_authoring_report.json'
    require(path.is_relative_to(directory) and len(str(path)) < 260, 'Keep timing evidence in the dedicated short path.')
    modules = _modules()
    backend, worklist = modules['animation_composition'], modules['animation_worklist']
    report = {'schema': 'x.validation.character-composition-loop-authoring/1',
              'complete': False, 'pendingNativeOperator': False, 'config': config}
    phase = 'authenticate_actual_prepared_HoldLoop'
    try:
        row = _row(context, config['holdLoopWorkItemId'], modules, config['expectedModelSha256'])
        require(not row.new_animation and not row.new_action_id, 'The third Loop must use its genuine prepared SOURCE Link.')
        _sealed_loop(backend.item_spec(row))
        saved, rig = worklist.state(context), row.rig
        prior_workspace = (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256)
        prior = backend._prior(context, rig)
        untouched = [(item.custom_action, item.custom_slot, backend._motion_hash(item.custom_action, item.custom_slot))
                     for item in saved.items if item.custom_action and item.item_id != row.item_id]
        sources = [(item.source_action, item.source_slot, backend._motion_hash(item.source_action, item.source_slot))
                   for item in saved.items if item.source_action]
        phase = 'author_actual_HoldLoop_Custom'
        report['operator_diagnostics'] = {}
        def checkpoint():
            diagnostic = report['operator_diagnostics']
            report.update(pendingNativeOperator=diagnostic['pendingNativeOperator'],
                          checkpoint_stage=diagnostic['checkpoint_stage'], checkpoint_phase=phase)
            _write(path, report)
        report['authoring'] = _author_loop(context, row, modules, config['holdLoopRotationControl'],
                                         diagnostics=report['operator_diagnostics'], checkpoint=checkpoint)
        phase = 'measure_genuine_foreground_B_for_physical_Sync'
        report['numeric_proof'] = _numeric_proof(context, row, modules)
        require(all(backend._motion_hash(action, slot) == original for action, slot, original in untouched + sources)
            and prior_workspace == (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256)
            and backend._prior(context, rig) == prior and not backend.active(rig),
            'Pre-publication authoring changed another Action, Source, workspace or prior native binding.')
        report.update(complete=True, only_explicit_HoldLoop_Custom_authored=True,
                      all_other_Customs_Sources_unchanged=True, prior_state_restored=True)
        _write(path, report)
        return report
    except Exception:
        report['failure_phase'], report['traceback'] = phase, traceback.format_exc()
        _write(path, report)
        raise


def measure_registered_raise(context, *, work_item_id, model_sha256):
    """Measure the caller-authored genuine NEW Raise for explicit physical Sync."""
    modules = _modules()
    row = _row(context, work_item_id, modules, model_sha256)
    spec = modules['animation_composition'].item_spec(row)
    require(row.new_animation and row.new_action_id and spec['timeMode'] == 'Raise',
            'Measure the genuinely registered native NEW Raise, never an edited SOURCE DTO.')
    _sealed_raise(spec)
    return _numeric_proof(context, row, modules)


def run(context, config):
    """Verify the collected moving third Loop and preauthored genuine NEW Raise."""
    config = validate_config(config)
    directory = Path(config['evidenceDirectory']).resolve()
    require(directory.is_dir(), 'Create the dedicated isolated timing evidence directory first.')
    path = directory / 'composition_timing_acceptance_report.json'
    require(path.is_relative_to(directory) and len(str(path)) < 260, 'Keep timing evidence in the dedicated short path.')
    modules = _modules()
    backend, worklist = modules['animation_composition'], modules['animation_worklist']
    report = {'schema': REPORT_SCHEMA, 'complete': False, 'phases': [], 'config': config}
    phase = 'authenticate_actual_rows'
    try:
        loop, raise_row, hold = [_row(context, config[key], modules, config['expectedModelSha256']) for key in
            ('holdLoopWorkItemId', 'raiseWorkItemId', 'followingHoldWorkItemId')]
        ls, rs, hs = map(backend.item_spec, (loop, raise_row, hold))
        _sealed_clocks(ls, rs, hs)
        require(loop.item_id == hold.item_id and not loop.new_animation and not loop.new_action_id
                and raise_row.new_animation and raise_row.new_action_id,
                'Use the collected third SOURCE Loop and its genuine separately registered NEW Raise.')
        require(uuid.UUID(rs['holdWorkItemId']) == uuid.UUID(hold.work_item_id)
                and rs['holdNewActionId'] == hs['editNewActionId'], 'Raise does not name this exact actual Hold destination.')
        require(loop.rig is raise_row.rig is hold.rig and not backend.active(loop.rig), 'Timing rows must share one idle adopted Main Rig.')
        saved = worklist.state(context)
        rig = loop.rig
        prior_workspace = (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256)
        prior = backend._prior(context, rig)
        untouched = [(row, backend._motion_hash(row.custom_action, row.custom_slot)) for row in saved.items
                     if row.custom_action]
        source_proofs = [(row.source_action, row.source_slot, backend._motion_hash(row.source_action, row.source_slot))
                         for row in saved.items if row.source_action]
        require(ls['editPhase'] % 1. != 0. or ls['editSpeed'] != 1. or ls['fps'] != rs['fps'],
                'The Loop fixture must exercise a non-default own phase/speed/rate.')
        report['actual_rows'] = [{'work_item_id': row.work_item_id, 'item_id': row.item_id,
            'slot': row.custom_slot, 'mode': spec['timeMode'], 'packet_sha256': backend._packet_for_item(row)['_sha256']}
            for row, spec in zip((loop, raise_row, hold), (ls, rs, hs))]
        _write(path, report)

        phase = 'prove_actual_collected_following_Hold'
        report['phases'].append({'phase': phase, **_published_hold(context, hold, modules, config)})
        _write(path, report)

        cycle = (ls['sampleEnd'] - ls['sampleStart']) / ls['editSpeed']
        loop_seeks = [.137 * cycle, .537 * cycle, .937 * cycle]
        wrap = (1. - ls['editPhase'] % 1.) * cycle
        if 1e-5 < wrap < cycle - 1e-5:
            wrap_epsilon = min(1e-5 / ls['fps'], wrap * 1e-5, (cycle - wrap) * 1e-5)
            loop_seeks.extend([wrap - wrap_epsilon, wrap + wrap_epsilon])
        phase = 'HoldLoop_own_clock'
        loop_raw = [b_clock(ls, seconds)['seconds'] for seconds in loop_seeks]
        expected_loop = _isolated_samples(context, loop, loop_raw, modules)
        expected_loop_a = _isolated_a_samples(context, loop, loop_seeks, modules)
        loop_result = _compare(context, loop, loop_seeks, expected_loop, expected_loop_a, modules, config)
        report['phases'].append({'phase': phase, **loop_result})
        _write(path, report)

        transition = (rs['sampleEnd'] - rs['sampleStart']) * (1. - rs['editPhase']) / rs['editSpeed']
        require(transition > 0, 'The true Raise fixture must expose a positive Raise segment.')
        epsilon = min(transition * 1e-5, 1e-5 / rs['fps'])
        hold_cycle = (hs['sampleEnd'] - hs['sampleStart']) / hs['editSpeed']
        raise_seeks = [.137 * transition, .537 * transition, transition - epsilon,
                       transition, transition + epsilon, transition + .237 * hold_cycle,
                       transition + .737 * hold_cycle, transition + .937 * hold_cycle]
        raw = [b_clock(rs, seconds, following=hs) for seconds in raise_seeks]
        phase = 'Raise_to_real_Hold_own_clock'
        authored_times = [clock['seconds'] for clock in raw if clock['role'] == 'B']
        hold_times = [clock['seconds'] for clock in raw if clock['role'] == 'HOLD']
        authored = iter(_isolated_samples(context, raise_row, authored_times, modules))
        held = iter(_isolated_samples(context, hold, hold_times, modules))
        expected_raise = [next(authored) if clock['role'] == 'B' else next(held) for clock in raw]
        expected_raise_a = _isolated_a_samples(context, raise_row, raise_seeks, modules)
        raise_result = _compare(context, raise_row, raise_seeks, expected_raise, expected_raise_a, modules, config, following=hs)
        endpoint_error = _max_error(expected_raise[2]['pose'], expected_raise[3]['pose'], expected_raise[3]['pose'])
        require(endpoint_error <= config['poseTolerance'], 'The genuine authored Raise endpoint does not meet its actual following Hold pose.')
        report['phases'].append({'phase': phase, **raise_result,
            'actual_Hold_work_item_id': hold.work_item_id, 'endpoint_continuity_error': endpoint_error,
            'boundary_epsilon_seconds': epsilon})
        _write(path, report)

        phase = 'measure_genuine_NEW_Raise_for_explicit_physical_Sync'
        report['raise_numeric_proof'] = _numeric_proof(context, raise_row, modules)
        _write(path, report)

        phase = 'preserve_original_actions_workspace_and_source'
        require(all(backend._motion_hash(row.custom_action, row.custom_slot) == original for row, original in untouched)
            and all(backend._motion_hash(action, slot) == original for action, slot, original in source_proofs),
            'Timing checks changed a protected Custom/Source, Loop, Raise or actual following Hold.')
        require(prior_workspace == (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256)
                and backend._prior(context, rig) == prior and not backend.active(rig),
                'Timing checks changed historical workspace or did not restore exact artist binding/pose/mode/NLA.')
        report['phases'].append({'phase': phase, 'no_Action_curves_authored': True,
                                 'all_Customs_Sources_unchanged': True, 'prior_state_restored': True})
        report['complete'] = True
        _write(path, report)
        return report
    except Exception:
        report['failure_phase'], report['traceback'] = phase, traceback.format_exc()
        _write(path, report)
        raise
