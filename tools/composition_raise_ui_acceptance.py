"""Real-screen, curve-read-only NEW Raise checks inside an admitted QA copy.

Importing starts nothing. The dedicated runner authenticates the completed
second-stage seed and final publication before passing plain admission data.
Every yield returns to the reusable native screenshot controller.
"""
import hashlib
import importlib
import json
import math
from pathlib import Path


def require(value, message):
    if not value:
        raise AssertionError(message)


def checkpoint_times(spec):
    """Independent seconds clock, excluding the FBX rest frame."""
    require(spec['timeMode'] == 'Raise', 'A genuine Raise DTO is required.')
    values = [spec[key] for key in ('sampleStart', 'sampleEnd', 'fps', 'editSpeed', 'editPhase')]
    require(all(type(value) in (int, float) and math.isfinite(value) for value in values),
            'Raise clock values must be finite.')
    start, end, fps, speed, phase = values
    duration = end - start
    require(duration > 0 and fps > 0 and speed > 0 and 0 <= phase < .5,
            'This real authored Raise must expose its middle key before the Hold transition.')
    transition = duration * (1. - phase) / speed
    seconds = [.137 * transition, duration * (.5 - phase) / speed, .537 * transition]
    require(len(set(seconds)) == 3 and all(0 <= value < transition for value in seconds),
            'Every Raise visual seek must precede the actual Hold transition.')
    return {'transition_seconds': transition, 'C_seconds': seconds,
            'C_frames': [1. + value * fps for value in seconds],
            'B_local_seconds': [start + phase * duration + value * speed for value in seconds],
            'phase_speed_applied_once': True}


def _plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _modules():
    return {name: importlib.import_module('character_designer.' + name) for name in (
        'animation_composition', 'animation_worklist', 'animation_worklist_target',
        'unity_animation', 'animation_link', 'animation_worklist_new',
        'animation_composition_export', 'animation_composition_weapon',
        'animation_worklist_fingerprint')}


def _current(item_id, admission, modules):
    import bpy
    context = bpy.context
    require(context.window is not None and context.scene.name == admission['sceneName'],
            'Keep the exact owned QA Scene in the headful window.')
    saved = modules['animation_worklist'].state(context)
    item = modules['animation_worklist']._item(saved, item_id)
    require(item.rig is saved.rig and item.rig.name == admission['rigName']
            and item.rig.name in context.scene.objects and item.new_animation
            and item.item_id == admission['nativeItemId']
            and item.work_item_id == admission['nativeWorkItemId']
            and item.new_action_id == admission['nativeNewActionId']
            and item.custom_slot == admission['slot']
            and sorted(scene.name for scene in item.rig.users_scene) == admission['rigSceneNames'],
            'The unique actual NEW Raise/native slot/Rig membership changed.')
    require(Path(saved.workspace_path).resolve() == Path(admission['workspacePath']).resolve()
            and saved.workspace_identity == admission['workspaceIdentity']
            and Path(saved.composition_workspace_path).resolve() == Path(admission['workspacePath']).resolve()
            and saved.composition_workspace_identity == admission['workspaceIdentity'],
            'Keep the exact same current QA workspace/Composition Library identity.')
    return context, saved, item


def action_proofs(modules):
    """Portable selected-slot contents survive real save/reopen; no RNA cache."""
    import bpy
    fp = modules['animation_worklist_fingerprint']
    result = []
    for action in bpy.data.actions:
        for slot in tuple(action.slots) or (None,):
            handle = slot.handle if slot else 0
            proof = fp.fingerprint_action(action, handle)
            require(proof['fingerprint_known'], 'An existing Action has unproved content: ' + action.name)
            result.append({'name': action.name,
                'library': str(Path(bpy.path.abspath(action.library.filepath)).resolve()) if action.library else '',
                'slot': handle, 'proof': _plain(proof)})
    return result


def assert_actions(proofs, modules):
    current = {(row['name'], row['library'], row['slot']): row['proof'] for row in action_proofs(modules)}
    require(all(current.get((row['name'], row['library'], row['slot'])) == row['proof'] for row in proofs),
            'A protected prior Action or exact slot changed or disappeared.')


def portable_rig(context, rig, modules):
    """Original geometry and binding/pose/context, without transient ID pointers."""
    backend, ua = modules['animation_composition'], modules['unity_animation']
    ad = rig.animation_data
    def identity(action):
        import bpy
        return None if action is None else [action.name,
            str(Path(bpy.path.abspath(action.library.filepath)).resolve()) if action.library else '']
    return _plain({'rest': ua._rest_state(rig.data), 'data_name': rig.data.name,
        'original_rest': ua._rest_state(modules['animation_worklist_target']._load(rig)['original_data']),
        'snapshot': ua._snapshot(context, rig), 'mode': rig.mode,
        'action': identity(ad.action) if ad else None,
        'nla': [{'name': track.name, 'mute': track.mute, 'solo': track.is_solo,
                 'strips': [{'name': strip.name, 'action': identity(strip.action),
                    'slot': strip.action_slot_handle,
                    'fields': {field: backend._plain(getattr(strip, field)) for field in backend._STRIP_FIELDS}}
                    for strip in track.strips]} for track in ad.nla_tracks] if ad else [],
        'object': {field: list(getattr(rig, field)) for field in (
            'location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale',
            'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')},
        'world': [float(value) for row in rig.matrix_world for value in row],
        'units': {'system': context.scene.unit_settings.system,
                  'scale_length': context.scene.unit_settings.scale_length}})


def guard(item_id, admission, modules, session_helper, initial_actions, rest):
    context, saved, item = _current(item_id, admission, modules)
    target, backend = modules['animation_worklist_target'], modules['animation_composition']
    require(target._load(item.rig)['scene'] is context.scene and saved.target_guid == admission['targetGuid']
            and saved.model_sha256 == admission['modelSha256'], 'The QA adoption owner/model/target changed.')
    if backend.active(item.rig):
        # ensure_active admits ordinary Worklist Actions and forbids native
        # Tweak. Composition owns its exact draft/strip and validates that
        # authenticated transaction, full Rest and current scope itself.
        backend.validate(context, item.rig)
    else:
        target.ensure_active(context, item.rig)
    session_helper.visual_guard(context, item_id, {'addonModule': 'character_designer'})
    ua = modules['unity_animation']
    require(ua._rest_state(item.rig.data) == rest['motion']
            and ua._rest_state(target._load(item.rig)['original_data']) == rest['original'],
            'A full protected/current Armature Rest field changed.')
    require(backend.item_spec(item) == admission['spec'], 'The actual NEW Raise DTO changed.')
    if backend.active(item.rig):
        weapon = modules['animation_composition_weapon']
        require(weapon.RECORD in item.rig, 'Keep the actual owned gun present during the Raise visual checkpoints.')
        record, _objects = weapon.validate_owned(item.rig)
        proof = json.loads(record['proof_json'])
        require(proof['context_sha256'] == admission['spec']['weaponContextSha256']
                and proof['source_sha256'] == admission['spec']['weaponModelSha256'],
                'The displayed gun/context changed during visual review.')
    assert_actions(initial_actions, modules)


def _steps(item_id, admission, config, session_helper, timing, preservation):
    """Show actual moving Raise C/Edit B, playback, save/reopen and explicit Resume.

    No transform/key/capture/Sync is called. User input that edits a curve is
    rejected by the protected Action guard. Cancellation keeps the QA draft.
    """
    import bpy
    modules = _modules()
    backend, worklist, ua, weapon = (modules[name] for name in (
        'animation_composition', 'animation_worklist', 'unity_animation', 'animation_composition_weapon'))
    context, saved, item = _current(item_id, admission, modules)
    require(not backend.active(item.rig), 'The admitted NEW Raise seed must be idle.')
    directory = Path(config['evidenceDirectory']).resolve()
    active_file, closed_file = directory / 'raise_active_session.blend', directory / 'raise_closed_session.blend'
    require(all(not path.exists() and len(str(path)) < 260 for path in (active_file, closed_file)),
            'Retain prior UI saves; use fresh owned filenames.')
    report = {'schema': 'x.validation.character-composition-raise-ui-session/1', 'complete': False,
        'native_item_id': item_id, 'public_work_item_id': admission['publicWorkItemId'],
        'public_new_action_id': admission['publicNewActionId'], 'phases': []}
    spec, clocks = admission['spec'], checkpoint_times(admission['spec'])
    worklist.activate(context, item_id, 'CUSTOM')
    base, base_slot = backend.prepare_reference(context, item)
    require(base.library is not None, 'Use the actual linked readonly A Source.')
    actions = action_proofs(modules)
    rest = {'motion': ua._rest_state(item.rig.data),
            'original': ua._rest_state(modules['animation_worklist_target']._load(item.rig)['original_data'])}
    prior = portable_rig(context, item.rig, modules)
    assets = preservation.asset_snapshot(context.scene, item.rig, ua)
    b_hash, a_hash = backend._motion_hash(item.custom_action, item.custom_slot), backend._motion_hash(base, base_slot)
    # Expectations sample canonical raw B and readonly raw A independently of
    # the native NLA map. _isolated_samples opens/closes only disposable clones.
    tm = timing._modules()
    expected_b = timing._isolated_samples(context, item, clocks['B_local_seconds'], tm)
    expected_a = timing._isolated_a_samples(context, item, clocks['C_seconds'], tm)
    require(max(timing._max_error(expected_b[0]['pose'], row['pose'], expected_b[0]['pose'])
                for row in expected_b[1:]) > 1e-5, 'The checkpoint poses are not a genuinely moving Raise.')
    require(portable_rig(context, item.rig, modules) == prior, 'Independent raw sampling changed the prior native state.')
    assert_actions(actions, modules)
    require(bpy.ops.character_designer.composition_open('EXEC_DEFAULT', True) == {'FINISHED'},
            'The actual UI Open Composition operator failed for NEW Raise.')
    require(bpy.ops.character_designer.composition_target('EXEC_DEFAULT', True, target='B') == {'FINISHED'}
            and bpy.ops.character_designer.composition_view('EXEC_DEFAULT', True, solo=False) == {'FINISHED'},
            'The actual UI must explicitly choose Edit B and Full Stack.')
    context, saved, item = _current(item_id, admission, modules)
    session, _item, scope, tracks = backend.validate(context, item.rig)
    owner, draft_hash = session['owner'], backend._motion_hash(session['edit_action'], session['edit_slot'])
    require(session['edit_target'] == 'B' and not session['solo'] and item.rig.animation_data.use_tweak_mode
            and item.rig.animation_data.action is session['edit_action'] and len(scope.mapping.indices) == 217,
            'The actual NEW Raise did not enter native Full Stack Tweak / Edit B.')
    hold_item = worklist._item(saved, session['following_hold']['item_id'])
    require(hold_item.work_item_id == admission['nativeHoldWorkItemId'], 'A different following Hold was installed.')
    timing._assert_strip_clocks(session, tracks, backend, backend.item_spec(hold_item))
    require(weapon.RECORD not in item.rig, 'Begin the Raise weapon screen check with a clean owned display.')
    ua._set_frame(context.scene, clocks['C_frames'][0])
    require(bpy.ops.character_designer.composition_weapon('EXEC_DEFAULT', True, remove=False) == {'FINISHED'},
            'Actual Load Weapon UI operator failed.')
    record, objects = weapon.validate_owned(item.rig)
    weapon_proof = _plain(json.loads(record['proof_json']))
    document, _expected_surface = weapon.validate_context(spec)
    captured = [row for row in document['meshes'] if row['enabled'] and row['activeInPrefab']]
    displayed = [obj for obj in objects if obj.type == 'MESH']
    captured_counts = {'captured_renderer_count':len(captured),
        'captured_vertex_count':sum(row['vertexCount'] for row in captured),
        'captured_triangle_count':sum(len(row['triangles']) // 3 for row in captured)}
    native_counts = {'captured_renderer_count':len(displayed),
        'captured_vertex_count':sum(len(obj.data.vertices) for obj in displayed),
        'captured_triangle_count':sum(len(obj.data.polygons) for obj in displayed)}
    require(captured and displayed and all(isinstance(obj.get('captured_renderer_path'), str) for obj in displayed)
        and all(len(face.vertices) == 3 for obj in displayed for face in obj.data.polygons)
        and len({obj.data for obj in displayed}) == len(displayed)
        and {obj.data for obj in displayed} == set(record['assets'].get('meshes', {}).values())
        and sorted((obj['captured_renderer_path'],len(obj.data.vertices),len(obj.data.polygons)) for obj in displayed)
            == sorted((row['rendererPath'],row['vertexCount'],len(row['triangles']) // 3) for row in captured),
        'Owned native weapon meshes differ from the authenticated active captured renderers.')
    require(weapon_proof.get('captured_unity_geometry') is True and weapon_proof.get('actual_fbx_import') is False
        and all(type(weapon_proof.get(key)) is int and weapon_proof[key] > 0
                and weapon_proof[key] == value == native_counts[key] for key,value in captured_counts.items())
        and weapon_proof.get('prefab_scale_applied_once') is True
        and weapon_proof.get('context_sha256') == spec['weaponContextSha256']
        and weapon_proof.get('source_sha256') == spec['weaponModelSha256']
        and weapon_proof.get('placed_surface_proved') is True
        and all(type(weapon_proof.get(key)) in (int, float) and math.isfinite(weapon_proof[key]) and 0 <= weapon_proof[key] <= 2e-5
                for key in ('placed_surface_error_metres','placed_bounds_error_metres'))
        and type(weapon_proof.get('placed_distinct_vertices')) is int
        and 4 <= weapon_proof['placed_distinct_vertices'] <= captured_counts['captured_vertex_count']
        and weapon_proof.get('automatic_hand_ik') is False,
        'Captured weapon geometry/context/counts/scale or its placed surface proof was not authenticated.')
    del document, _expected_surface, captured, displayed
    del record, objects, base, hold_item
    samples = []
    for index, seconds in enumerate(clocks['C_seconds']):
        context, saved, item = _current(item_id, admission, modules)
        session, _item, scope, tracks = backend.validate(context, item.rig)
        ua._set_frame(context.scene, clocks['C_frames'][index])
        actual = timing._pose(context, item.rig, scope.mapping.indices)
        upper_error = timing._max_error(actual, expected_b[index]['pose'], scope.native_names)
        lower_error = timing._max_error(actual, expected_a[index]['pose'], expected_a[index]['pose'])
        require(max(upper_error, lower_error) <= config['poseTolerance'],
                'Real C differs from independent raw Raise/A pose clocks.')
        session_helper._show_control(context, item, scope)
        state = {'C_seconds': seconds, 'B_local_seconds': clocks['B_local_seconds'][index],
                 'upper_vs_raw_Raise': upper_error, 'lower_vs_raw_A': lower_error}
        samples.append(state)
        yield {**session_helper._checkpoint(context, item, modules, 'new_raise_edit_b_%d' % (index + 1),
            'Actual NEW Raise C · Editing B · moving upper-body controls'), **state,
            'public_new_action_id': admission['publicNewActionId'],
            'public_work_item_id': admission['publicWorkItemId'], 'weapon_proof': weapon_proof,
            'allowed_control_names': sorted(set(scope.input_by_native.values()))}
    context, saved, item = _current(item_id, admission, modules)
    require(not context.window.screen.is_animation_playing, 'Start the Raise playback check paused.')
    require(bpy.ops.screen.animation_play() == {'FINISHED'} and context.window.screen.is_animation_playing,
            'Actual native Raise C playback did not start.')
    yield session_helper._checkpoint(context, item, modules, 'new_raise_playing', 'Actual NEW Raise C · native playback')
    context, saved, item = _current(item_id, admission, modules)
    require(context.window.screen.is_animation_playing
            and bpy.ops.screen.animation_cancel(restore_frame=False) == {'FINISHED'}
            and not context.window.screen.is_animation_playing, 'Actual native Raise playback did not pause.')
    ua._set_frame(context.scene, clocks['C_frames'][1])
    yield session_helper._checkpoint(context, item, modules, 'new_raise_paused', 'Actual NEW Raise C · paused at its moving middle key')
    context, saved, item = _current(item_id, admission, modules)
    session = backend.validate(context, item.rig)[0]
    require(backend._motion_hash(session['edit_action'], session['edit_slot']) == draft_hash,
            'A screen operation changed the read-only Raise draft.')
    require(bpy.ops.character_designer.composition_view('EXEC_DEFAULT', True, solo=True) == {'FINISHED'},
            'The native view operation for the Raise Undo check failed.')
    with session_helper._view3d(context, item.rig):
        require(bpy.ops.ed.undo() == {'FINISHED'}, 'Actual native Raise view Undo did not finish.')
    context, saved, item = _current(item_id, admission, modules)
    session = backend.validate(context, item.rig)[0]
    require(session['owner'] == owner and not session['solo']
            and backend._motion_hash(session['edit_action'], session['edit_slot']) == draft_hash,
            'Native Undo lost the same owned Raise draft or did not restore Full Stack.')
    with session_helper._view3d(context, item.rig):
        require(bpy.ops.ed.redo() == {'FINISHED'}, 'Actual native Raise view Redo did not finish.')
    context, saved, item = _current(item_id, admission, modules)
    session = backend.validate(context, item.rig)[0]
    require(session['owner'] == owner and session['solo']
            and backend._motion_hash(session['edit_action'], session['edit_slot']) == draft_hash,
            'Native Redo lost the same Raise draft or did not restore its explicit Solo view.')
    require(bpy.ops.character_designer.composition_view('EXEC_DEFAULT', True, solo=False) == {'FINISHED'},
            'Actual UI could not return Raise to Full Stack after Undo/Redo.')
    context, saved, item = _current(item_id, admission, modules)
    assert_actions(actions, modules)
    yield session_helper._checkpoint(context, item, modules, 'new_raise_undo_redo',
        'Actual NEW Raise · native Undo/Redo · Full Stack restored without curve edits')
    context, saved, item = _current(item_id, admission, modules)
    session, _item, scope, tracks = backend.validate(context, item.rig)
    require(bpy.ops.wm.save_as_mainfile(filepath=str(active_file), check_existing=False) == {'FINISHED'}
            and Path(bpy.data.filepath).resolve() == active_file, 'The separate active Raise save failed.')
    active_sha = _hash(active_file)
    # Do not carry native IDs through a real .blend replacement.
    del context, saved, item, session, scope, tracks, _item, tm
    require(bpy.ops.wm.open_mainfile(filepath=str(active_file), use_scripts=False, load_ui=False) == {'FINISHED'},
            'The active Raise fixture did not reopen.')
    bpy.context.window.scene = bpy.data.scenes[admission['sceneName']]
    context, saved, item = _current(item_id, admission, modules)
    session, _item, scope, tracks = backend.validate(context, item.rig)
    require(session['owner'] == owner
            and backend._motion_hash(session['edit_action'], session['edit_slot']) == draft_hash
            and backend._motion_hash(session['base_action'], session['base_slot']) == a_hash,
            'Save/reopen lost the owned Raise draft or changed readonly A.')
    require(bpy.ops.character_designer.composition_resume('EXEC_DEFAULT', True) == {'FINISHED'},
            'Actual NEW Raise Resume operator failed.')
    weapon.validate_owned(item.rig)
    assert_actions(actions, modules)
    session_helper._show_control(context, item, scope)
    yield session_helper._checkpoint(context, item, modules, 'new_raise_saved_reopened',
        'Actual NEW Raise · saved/reopened · explicitly resumed Edit B with actual gun')
    context, saved, item = _current(item_id, admission, modules)
    require(bpy.ops.character_designer.composition_weapon('EXEC_DEFAULT', True, remove=True) == {'FINISHED'}
            and weapon.RECORD not in item.rig, 'The actual owned gun could not be removed.')
    require(bpy.ops.character_designer.composition_close('EXEC_DEFAULT', True) == {'FINISHED'},
            'The actual NEW Raise Close operator failed.')
    context, saved, item = _current(item_id, admission, modules)
    require(not backend.active(item.rig) and not item.rig.animation_data.use_tweak_mode
            and backend._motion_hash(item.custom_action, item.custom_slot) == b_hash,
            'Read-only Raise Close changed B or retained native Tweak.')
    require(portable_rig(context, item.rig, modules) == prior,
            'Close did not restore original Rest/Action/slot/NLA/pose/time/units exactly.')
    assert_actions(actions, modules)
    require(not preservation.changes(assets, preservation.asset_snapshot(context.scene, item.rig, ua)),
            'Raw original colors/groups/widgets/meshes/Rest changed after Raise Close.')
    require(bpy.ops.wm.save_as_mainfile(filepath=str(closed_file), check_existing=False) == {'FINISHED'},
            'The separate closed Raise save failed.')
    closed_sha = _hash(closed_file)
    del context, saved, item, session, scope, tracks, _item
    require(bpy.ops.wm.open_mainfile(filepath=str(closed_file), use_scripts=False, load_ui=False) == {'FINISHED'},
            'The closed Raise fixture did not reopen.')
    bpy.context.window.scene = bpy.data.scenes[admission['sceneName']]
    context, saved, item = _current(item_id, admission, modules)
    require(not backend.active(item.rig) and portable_rig(context, item.rig, modules) == prior
            and backend._motion_hash(item.custom_action, item.custom_slot) == b_hash,
            'Closed reopen implicitly resumed or changed original protected state.')
    assert_actions(actions, modules)
    require(not preservation.changes(assets, preservation.asset_snapshot(context.scene, item.rig, ua)),
            'Closed reopen changed raw original assets.')
    report.update(complete=True, clocks=clocks, samples=samples,
        active_save={'path': str(active_file), 'sha256': active_sha},
        closed_save={'path': str(closed_file), 'sha256': closed_sha},
        actual_NEW_Raise_observed=True, native_Full_Stack_Edit_B=True,
        actual_play_pause=True, actual_view_undo_redo=True, explicit_resume=True, protected_A_B_actions_unchanged=True,
        original_native_state_restored=True, original_assets_raw_exact=True,
        curve_authoring_performed=False, worker_launch_performed=False, SourceSync_performed=False)
    return report


def run_steps(item_id, admission, config, session_helper, timing, preservation):
    """Restore the owned profile preference even when a timer is cancelled."""
    import bpy
    previous = bpy.context.preferences.edit.use_global_undo
    bpy.context.preferences.edit.use_global_undo = True
    try:
        return (yield from _steps(item_id, admission, config, session_helper, timing, preservation))
    finally:
        bpy.context.preferences.edit.use_global_undo = previous
