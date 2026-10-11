"""Root-invoked native NEW Raise authoring in its disposable Blender copy.

Importing starts nothing. run saves no .blend, launches no process and publishes
nothing. It creates a real local Action/NewAction manifest through register_b,
then keys only the declared FK inputs. This is timing QA, not pose art approval.
"""
import json
import math
from pathlib import Path
import re
import traceback
import uuid

import composition_timing_acceptance as timing

SCHEMA = 'x.validation.character-composition-raise-authoring-fixture/1'
REPORT_SCHEMA = 'x.validation.character-composition-raise-authoring/1'
FIELDS = {'schema', 'evidenceDirectory', 'preparedRaiseWorkItemId',
          'collectedHoldLoopWorkItemId', 'expectedModelSha256',
          'followingHoldPublication', 'rotationControl'}
_ANCHOR_FIELDS = ('item_id', 'work_item_id', 'new_action_id', 'new_animation',
    'clip_key', 'manifest_path', 'link_identity', 'model_file', 'model_sha256',
    'export_rig_name', 'workspace_path', 'workspace_identity', 'source_file',
    'source_hash', 'composition_json', 'composition_sha256', 'application_role', 'edit_scope')


def validate_config(config):
    timing.require(type(config) is dict and set(config) == FIELDS and config['schema'] == SCHEMA,
                   'Supply the exact genuine Raise authoring fixture.')
    held, prepared = (uuid.UUID(config[key]).hex for key in
                      ('collectedHoldLoopWorkItemId', 'preparedRaiseWorkItemId'))
    timing.require(held != prepared and all(int(value, 16) != 0 for value in (held, prepared)),
                   'Choose distinct genuine collected Loop and prepared SOURCE Raise rows.')
    timing.require(type(config['expectedModelSha256']) is str
        and re.fullmatch('[0-9a-f]{64}', config['expectedModelSha256']), 'Pin the current prepared model SHA.')
    timing.require(config['rotationControl'] is None or type(config['rotationControl']) is str,
                   'Choose null or one exact declared FK input control, never an alias.')
    # Reuse the bounded, real physical-publication admission without creating a receipt.
    timing.validate_config({'schema': timing.SCHEMA, 'evidenceDirectory': config['evidenceDirectory'],
        'holdLoopWorkItemId': held, 'followingHoldWorkItemId': held, 'raiseWorkItemId': prepared,
        'expectedModelSha256': config['expectedModelSha256'], 'poseTolerance': 2e-4,
        'inputTolerance': 1e-5, 'followingHoldPublication': config['followingHoldPublication']})
    return config


def _set_inputs(rig, scope, values):
    timing.require(len(values) == len(scope.channels), 'Every scoped input requires a real native value.')
    pending = []
    for (path, index), value in zip(scope.channels, values):
        array = rig.path_resolve(path)
        timing.require(hasattr(array, '__len__') and 0 <= index < len(array)
                       and type(value) in (int, float) and math.isfinite(value),
                       'Unsupported/nonfinite exact FK input: ' + path)
        pending.append((array, index, value))
    for array, index, value in pending:
        array[index] = value


def _key_inputs(rig, scope, frame):
    for path, index in scope.channels:
        timing.require(rig.keyframe_insert(data_path=path, index=index, frame=frame),
                       'The native selected-slot FK key failed: ' + path)


def _rotation_control(rig, scope, requested, *, diagnostics=None):
    from composition_session_acceptance import _pose_hidden
    matches = []
    for name in sorted(set(scope.input_by_native.values())):
        bone = rig.pose.bones[name]
        field = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                 'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        path = bone.path_from_id() + '.' + field
        indices = {index for channel, index in scope.channels if channel == path}
        visibility = timing._loop_visibility(rig, bone, _pose_hidden(bone))
        eligible = (indices == set(range(3 if field == 'rotation_euler' else 4))
                    and visibility['visible'] and not bone.bone.hide_select and not bone.lock_rotation[0])
        if diagnostics is not None:
            diagnostics['candidates'].append({'control': name, 'field': field,
                'scope_indices': sorted(indices), 'visibility': visibility,
                'hide_select': bone.bone.hide_select, 'lock_rotation': list(bone.lock_rotation),
                'eligible': eligible})
        if eligible:
            matches.append(name)
    if requested:
        matches = [name for name in matches if name == requested]
    timing.require(matches, 'No exact visible declared FK rotation control is available.')
    return matches[0]


def _created_identity(row, spec, created):
    timing.require(created['workItemId'] == spec['editWorkItemId']
        and created['newActionId'] == spec['editNewActionId'],
        'Actual manifest IDs must preserve the exact sealed DTO strings, not a different UUID representation.')
    return {'work_item_id': created['workItemId'], 'new_action_id': created['newActionId'],
        'native_row_work_id': row.work_item_id, 'native_row_new_action_id': row.new_action_id,
        'item_id': row.item_id, 'manifest_path': row.manifest_path, 'slot': row.custom_slot}


def _row_anchor(row, modules):
    """Freeze original native IDs and plain proof before a collection mutation."""
    backend = modules['animation_composition']
    packet = backend._packet_for_item(row)
    actions = []
    for action, slot in ((row.custom_action, row.custom_slot), (row.source_action, row.source_slot)):
        actions.append((action, slot, backend._motion_hash(action, slot) if action else None))
    return {'row': {field: getattr(row, field) for field in _ANCHOR_FIELDS},
        'spec': json.loads(json.dumps(backend.item_spec(row), allow_nan=False)),
        'manifest_sha256': timing._sha(row.manifest_path),
        'packet': (packet['_path'], packet['_sha256']), 'rig': row.rig, 'actions': tuple(actions)}


def _fresh_row(context, item_id, modules, model_hash, *, anchor=None):
    """Resolve an existing unique row after collection growth, never import it."""
    backend, worklist = modules['animation_composition'], modules['animation_worklist']
    saved = worklist.state(context)
    timing.require(type(item_id) is str and item_id
        and sum(item.item_id == item_id for item in saved.items) == 1,
        'The exact prior Raise/Loop Worklist row is missing or ambiguous.')
    row = worklist._item(saved, item_id)
    timing.require(row.model_sha256 == model_hash and backend.item_spec(row) is not None,
        'The exact Raise/Loop row lost its current model or sealed composition.')
    backend._packet_for_item(row)
    worklist._check_baseline(row)
    if anchor is not None:
        current = _row_anchor(row, modules)
        timing.require(current['row'] == anchor['row'] and current['spec'] == anchor['spec']
            and current['manifest_sha256'] == anchor['manifest_sha256'] and current['packet'] == anchor['packet']
            and current['rig'] is anchor['rig'] and all(action is original_action
                and slot == original_slot and content == original_content
                for (action, slot, content), (original_action, original_slot, original_content)
                in zip(current['actions'], anchor['actions'])),
            'A protected Raise/Loop row differs from its original pre-add native Action/spec/route anchor.')
    return row


def _author(context, row, held, held_seconds, modules, requested, *, diagnostics=None, checkpoint=None):
    import bpy
    from composition_session_acceptance import _pose_hidden, _view3d
    backend, ua = modules['animation_composition'], modules['unity_animation']
    diagnostics = diagnostics if diagnostics is not None else {}
    timing.require(type(bpy.app.background) is bool, 'Native background state must be an exact boolean.')
    undo_requested = not bpy.app.background
    diagnostics.update(requested_control=requested, candidates=[], frames=[],
        actualundo_requested=undo_requested, pendingNativeOperator=False, checkpoint_stage='authoring_started')
    held_sample = timing._isolated_samples(context, held, [held_seconds], modules)[0]
    with timing._open(context, held, modules) as (_session, _item, held_scope, _tracks):
        held_channels, held_names = tuple(held_scope.channels), tuple(held_scope.native_names)
    with timing._open(context, row, modules) as (session, _item, scope, tracks):
        timing.require(tuple(scope.channels) == held_channels and tuple(scope.native_names) == held_names,
                       'Raise and its collected Loop must have the exact same native FK input scope.')
        neutral = [scope.defaults[channel] for channel in scope.channels]
        endpoint = held_sample['inputs']
        name = _rotation_control(row.rig, scope, requested, diagnostics=diagnostics)
        bone = row.rig.pose.bones[name]
        field = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                 'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
        diagnostics.update(control=name, field=field, native_sources=sorted(
            native for native, input_name in scope.input_by_native.items() if input_name == name))
        start, end = backend._frame_range(session['spec'])
        before = backend._motion_hash(row.custom_action, row.custom_slot)
        keys = []
        with _view3d(context, row.rig) as view_context:
            for fraction in (0., .5, 1.):
                raw_frame = start + fraction * (end - start)
                scene_frame = row.rig.animation_data.nla_tweak_strip_time_to_scene(raw_frame)
                expected = tracks['EDIT'].strips[0].frame_start + (raw_frame - start) / session['spec']['editSpeed']
                timing.require(abs(scene_frame - expected) <= 1e-5,
                               'Native Raise Tweak key clock differs from its actual B-local frame.')
                ua._set_frame(context.scene, scene_frame)
                _set_inputs(row.rig, scope, endpoint if fraction == 1. else neutral)
                view_context.view_layer.update()
                frame_state = {'phase': fraction, 'B_frame': raw_frame, 'C_frame': scene_frame,
                    'actualundo_requested': undo_requested, 'checkpoint_stage': 'native_selection_readback'}
                diagnostics['frames'].append(frame_state)
                bone = timing._loop_select(view_context, row.rig, name, session, tracks,
                                           backend, _pose_hidden, frame_state)
                if fraction == .5:
                    operation = frame_state['operator'] = {'minimum_rotation_radians': 1e-7}
                    operation['before'] = timing._loop_rotation_state(bone, field)
                    timing.require(operation['before']['valid'], 'Native pre-transform Raise FK rotation is not finite/valid.')
                    operation['poll'] = bpy.ops.transform.rotate.poll()
                    timing.require(operation['poll'] is True, 'The actual native Raise rotation operator poll failed.')
                    operation.update(actualundo_requested=undo_requested, pendingNativeOperator=True,
                                     checkpoint_stage='before_native_rotate')
                    diagnostics.update(pendingNativeOperator=True, checkpoint_stage='before_native_rotate')
                    frame_state['checkpoint_stage'] = 'before_native_rotate'
                    if checkpoint is not None:
                        checkpoint()
                    try:
                        result = bpy.ops.transform.rotate('EXEC_DEFAULT', undo_requested, value=.073,
                            orient_axis='X', orient_type='LOCAL')
                    except BaseException:
                        operation['exception'] = traceback.format_exc()
                        raise
                    operation['result'] = sorted(result)
                    operation['after'] = timing._loop_rotation_state(row.rig.pose.bones[name], field)
                    timing.require(result == {'FINISHED'},
                        'The genuine middle native FK Raise transform did not finish.')
                    operation['rotation_delta_radians'] = timing._loop_rotation_delta(operation['before'], operation['after'])
                    timing.require(operation['rotation_delta_radians'] > 1e-7,
                                   'The finished native Raise FK rotation produced no actual rotation change.')
                    operation.update(pendingNativeOperator=False, checkpoint_stage='native_rotate_verified')
                    diagnostics.update(pendingNativeOperator=False, checkpoint_stage='native_rotate_verified')
                    frame_state['checkpoint_stage'] = 'native_rotate_verified'
                    if checkpoint is not None:
                        checkpoint()
                frame_state['keying'] = {'channel_count': len(scope.channels), 'complete': False}
                frame_state['checkpoint_stage'] = 'before_native_keys'
                diagnostics['checkpoint_stage'] = 'before_native_keys'
                if checkpoint is not None:
                    checkpoint()
                _key_inputs(row.rig, scope, scene_frame)
                frame_state['keying']['complete'] = True
                frame_state['checkpoint_stage'] = 'native_keys_verified'
                diagnostics['checkpoint_stage'] = 'native_keys_verified'
                if checkpoint is not None:
                    checkpoint()
                keys.append({'phase': fraction, 'B_frame': raw_frame, 'C_frame': scene_frame})
        middle = start + .5 * (end - start)
        curves = [curve for curve in backend._curves(session['edit_action'], session['edit_slot'])
                  if (curve.data_path, curve.array_index) in set(scope.channels)]
        timing.require(len(curves) == len(scope.channels)
            and all(any(abs(point.co[0] - middle) <= 1e-5 for point in curve.keyframe_points) for curve in curves),
            'A required native Raise FK channel lacks its B-local middle key.')
        backend.validate(context, row.rig)
        timing._assert_moving(session['edit_action'], session['edit_slot'], scope, session['spec'], backend)
        timing.require(backend.commit(context, row.rig), 'The genuine NEW Raise did not commit its own native Custom.')
        timing.require(backend._motion_hash(row.custom_action, row.custom_slot) != before,
                       'The native Raise keys did not change the genuine NEW Custom.')
    raw_end = timing._isolated_samples(context, row, [backend.item_spec(row)['sampleEnd']], modules)[0]
    error = timing._max_error(raw_end['pose'], held_sample['pose'], held_names)
    timing.require(error <= 2e-4, 'The independently evaluated raw Raise endpoint does not meet the collected Loop phase.')
    return {'native_FK_transform_and_keys': True, 'control': name, 'keys': keys,
        'endpoint_input_source': 'Actual collected Loop FK inputs at its own phase; no output matrix authoring.',
        'following_Hold_local_seconds': held_seconds, 'raw_endpoint_native_error': error,
        'pose_art_approval_claimed': False, 'native_upper_count': len(held_names)}


def run(context, config):
    """Register genuine NEW Raise, author native FK keys, restore prior binding."""
    import bpy
    config = validate_config(config)
    directory = Path(config['evidenceDirectory']).resolve()
    timing.require(directory.is_dir(), 'Create the dedicated isolated Raise evidence directory first.')
    path = directory / 'composition_Raise_authoring_report.json'
    timing.require(path.is_relative_to(directory) and len(str(path)) < 260,
                   'Keep Raise evidence in its dedicated short path.')
    modules = timing._modules()
    backend, worklist = modules['animation_composition'], modules['animation_worklist']
    report = {'schema': REPORT_SCHEMA, 'complete': False, 'pendingNativeOperator': False, 'config': config}
    row, candidate, prior, saved = None, None, None, None
    finished = False
    phase = 'authenticate_real_collected_Loop_and_prepared_SOURCE_Raise'
    try:
        # Adding the SOURCE template may reallocate every existing RNA row.
        held = timing._row(context, config['collectedHoldLoopWorkItemId'], modules, config['expectedModelSha256'])
        held_anchor = _row_anchor(held, modules)
        held_id = held_anchor['row']['item_id']
        held = None
        template = timing._row(context, config['preparedRaiseWorkItemId'], modules, config['expectedModelSha256'])
        template_anchor = _row_anchor(template, modules)
        template_id = template_anchor['row']['item_id']
        template = None
        held = _fresh_row(context, held_id, modules, config['expectedModelSha256'], anchor=held_anchor)
        template = _fresh_row(context, template_id, modules, config['expectedModelSha256'], anchor=template_anchor)
        hs, rs = backend.item_spec(held), backend.item_spec(template)
        timing._sealed_clocks(hs, rs, hs)
        timing.require(not held.new_animation and not template.new_animation and not rs['editNewActionId']
            and uuid.UUID(rs['holdWorkItemId']) == uuid.UUID(held.work_item_id)
            and rs['holdNewActionId'] == hs['editNewActionId'] and held.rig is template.rig,
            'Raise must be an actual prepared SOURCE template naming this genuine collected SOURCE Loop.')
        report['collected_Hold_proof'] = timing._published_hold(context, held, modules, config)
        saved, rig = worklist.state(context), template.rig
        prior = backend._prior(context, rig)
        prior_state = (saved.active_index, saved.status, saved.has_error)
        prior_sides = {item.item_id: item.side for item in saved.items}
        prior_workspace = (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256)
        protected = [(action, slot, backend._motion_hash(action, slot)) for item in saved.items
            for action, slot in ((item.custom_action, item.custom_slot), (item.source_action, item.source_slot)) if action]
        packets = [(str(item.item_id), _row_anchor(item, modules))
            for item in saved.items if backend.item_spec(item)]
        phase = 'register_genuine_local_NEW_Raise_from_native_prepared_template'
        worklist.activate(context, template_id, 'CUSTOM')
        candidate = template.custom_action.copy()
        candidate.name = 'Raise · Local Edit'
        worklist._clean_action(candidate)
        slot = worklist._slot(candidate, template.custom_slot)
        rig.animation_data.action, rig.animation_data.action_slot = candidate, slot
        held = template = None
        new_id = str(backend.register_b(context, template_id).item_id)
        row = _fresh_row(context, new_id, modules, config['expectedModelSha256'])
        held = _fresh_row(context, held_id, modules, config['expectedModelSha256'], anchor=held_anchor)
        template = _fresh_row(context, template_id, modules, config['expectedModelSha256'], anchor=template_anchor)
        saved = worklist.state(context)
        new_spec = backend.item_spec(row)
        timing.require(row.new_animation and row.custom_action is candidate and row.custom_slot == slot.handle
            and row.work_item_id != template.work_item_id and row.new_action_id
            and uuid.UUID(new_spec['holdWorkItemId']) == uuid.UUID(held.work_item_id)
            and backend._packet_for_item(row)['_sha256'] == backend._packet_for_item(template)['_sha256'],
            'Official registration did not create a separate actual NEW Raise with the exact sealed native template.')
        created = backend._host('animation_worklist_new').load(row.manifest_path)
        report['actual_NEW_identity'] = _created_identity(row, new_spec, created)
        held_seconds = hs['sampleStart'] + (hs['editPhase'] % 1.) * (hs['sampleEnd'] - hs['sampleStart'])
        phase = 'author_actual_scoped_native_FK_Raise'
        report['operator_diagnostics'] = {}
        def checkpoint():
            diagnostic = report['operator_diagnostics']
            report.update(pendingNativeOperator=diagnostic['pendingNativeOperator'],
                          checkpoint_stage=diagnostic['checkpoint_stage'], checkpoint_phase=phase)
            timing._write(path, report)
        report['authoring'] = _author(context, row, held, held_seconds, modules, config['rotationControl'],
                                     diagnostics=report['operator_diagnostics'], checkpoint=checkpoint)
        phase = 'measure_genuine_NEW_Raise_foreground_numeric_proof'
        report['numeric_proof'] = timing.measure_registered_raise(context,
            work_item_id=row.work_item_id, model_sha256=config['expectedModelSha256'])
        timing.require(all(backend._motion_hash(action, handle) == old for action, handle, old in protected)
            and all(backend._packet_for_item(_fresh_row(context, item_id, modules,
                config['expectedModelSha256'], anchor=anchor))['_sha256'] == anchor['packet'][1]
                for item_id, anchor in packets)
            and prior_workspace == (saved.workspace_path, saved.workspace_identity, saved.model_file, saved.model_sha256),
            'Raise creation changed a protected prior Custom, Source, sealed packet or workspace.')
        report.update(protected_Customs_Sources_packets_unchanged=True,
                      genuine_official_NewAction_registration=True,
                      worker_launch_performed=False, publication_performed=False)
        finished = True
        return report
    except Exception:
        report['failure_phase'], report['traceback'] = phase, traceback.format_exc()
        raise
    finally:
        try:
            if prior is not None:
                rig = saved.rig
                if backend.active(rig):
                    backend.exit(context, rig, writeback=False)
                backend._restore_prior(context, rig, prior)
                for item in saved.items:
                    if item.item_id in prior_sides:
                        item.side = prior_sides[item.item_id]
                saved.active_index, saved.status, saved.has_error = prior_state
                context.view_layer.update()
                timing.require(backend._prior(context, rig) == prior,
                               'Raise authoring did not restore the exact prior native binding/pose/mode/NLA.')
                report['prior_state_restored'] = True
            if candidate is not None and row is None:
                candidate.use_fake_user = False
                if candidate.users == 0:
                    bpy.data.actions.remove(candidate)
            report['complete'] = finished
        except Exception:
            report['complete'] = False
            report['failure_phase'], report['traceback'] = 'restore_prior_native_state', traceback.format_exc()
            raise
        finally:
            timing._write(path, report)
