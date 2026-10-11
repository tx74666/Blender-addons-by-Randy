"""Root-invoked UI acceptance on an immutable, hash-pinned successful QA seed.

Importing this tool launches nothing. Root must supply a fresh serial/native
window and invoke it inside a separate Blender process with --fixture. It only
opens the saved QA seed; the session helper owns its dedicated edit/save/reopen
lifecycle. It never enters the numeric driver's recovery scope or publishes B.
"""
import argparse
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import traceback
import uuid

SCHEMA = 'x.validation.character-composition-ui-fixture/1'
REPORT_SCHEMA = 'x.validation.character-composition-ui-run/1'
SEED_SCHEMA = 'x.validation.character-composition-qa-seed/1'
FIELDS = {'schema', 'inputBlend', 'inputBlendSha256', 'addonPackage', 'runtimeManifest',
    'runtimeManifestSha256', 'evidenceDirectory', 'numericReport', 'numericReportSha256',
    'seedBlend', 'seedBlendSha256', 'workItemId', 'sceneName', 'rigName', 'targetGuid',
    'modelSha256', 'sessionHelper', 'sessionConfig', 'currentWorkspace',
    'historicalWorkspaceSnapshot', 'sourceCompletionClosure', 'sourceCompletedFileAudit',
    'preparedRaiseWorkItemId'}
OPTIONAL_FIELDS = {'quitOnCompletion'}
SEED_FIELDS = {'schema', 'seedBlend', 'seedBlendSha256', 'sceneName', 'rigName', 'targetGuid',
    'modelSha256', 'workspacePath', 'workspaceSha256', 'workItemIds', 'itemIds',
    'physicalReport', 'physicalReportSha256', 'rigSceneNames'}
SESSION_FIELDS = {'createAWorkCopy', 'seekSeconds', 'poseTolerance', 'rotationControl',
    'aRotationControl', 'aWorkItemId', 'headfulCheckpoints'}
WORKSPACE_PROOF_FIELDS = ('currentWorkspace', 'historicalWorkspaceSnapshot',
    'sourceCompletionClosure', 'sourceCompletedFileAudit')
SOURCE_STAGE_SHA256 = '77161b99de9d63ecc698fd00bdb1f037525c1073f9e69b7f6efc0690e1580dfc'


def _checkpoint_api():
    path = Path(__file__).with_name('composition_ui_checkpoints.py')
    spec = importlib.util.spec_from_file_location('_composition_ui_checkpoint_admission', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _absolute(value):
    return type(value) is str and Path(value).is_absolute() and not any(ord(char) < 32 for char in value)


def _id(value):
    require(type(value) is str, 'Supply an actual UUID identity, never a display name.')
    try:
        result = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise AssertionError('Supply an actual UUID identity, never a display name.') from error
    require(result.int != 0, 'The actual identity cannot be the zero UUID.')
    return result.hex


def validate_fixture(config):
    """Pure admission; example IDs in tests never become native fixture rows."""
    require(type(config) is dict and FIELDS <= set(config) <= FIELDS | OPTIONAL_FIELDS and config['schema'] == SCHEMA,
        'Incomplete or unsupported standalone UI fixture.')
    require(all(_absolute(config[key]) for key in ('inputBlend', 'addonPackage', 'runtimeManifest',
        'evidenceDirectory', 'numericReport', 'seedBlend')), 'Pin absolute immutable input and evidence paths.')
    require(all(_sha(config[key]) for key in ('inputBlendSha256', 'runtimeManifestSha256',
        'numericReportSha256', 'seedBlendSha256', 'modelSha256')), 'Pin exact SHA-256 values for all inputs.')
    require(Path(config['inputBlend']).name.lower() != 'x.blend'
        and Path(config['inputBlend']).suffix.lower() == '.blend'
        and Path(config['seedBlend']).suffix.lower() == '.blend'
        and Path(config['seedBlend']).resolve() != Path(config['inputBlend']).resolve(),
        'Use the saved-artist validation copy and a separate immutable QA seed, never artist X.blend.')
    require(type(config['sceneName']) is str and config['sceneName'].strip()
        and config['rigName'] == 'CoshaRig' and type(config['targetGuid']) is str
        and re.fullmatch('[0-9a-f]{32}', config['targetGuid']), 'Pin the exact QA Scene and actual CoshaRig target.')
    _id(config['workItemId'])
    require(config['preparedRaiseWorkItemId'] is None or
        _id(config['preparedRaiseWorkItemId']) != _id(config['workItemId']),
        'Pin the separately prepared actual Raise work item, never the selected Hold Pose identity.')
    for key in WORKSPACE_PROOF_FIELDS:
        value = config[key]
        require(type(value) is dict and set(value) == {'path', 'sha256'}
            and _absolute(value['path']) and _sha(value['sha256']),
            'Pin the exact completed Source proof and historical/current workspace bytes: ' + key)
    helper = config['sessionHelper']
    require(type(helper) is dict and set(helper) == {'path', 'sha256'}
        and _absolute(helper['path']) and _sha(helper['sha256']), 'Pin the exact session acceptance helper bytes.')
    checks = config['sessionConfig']
    require(type(checks) is dict and set(checks) <= SESSION_FIELDS
        and {'createAWorkCopy', 'seekSeconds', 'poseTolerance'} <= set(checks)
        and type(checks['createAWorkCopy']) is bool, 'Use explicit supported session checks; the runner owns their output directory.')
    require('quitOnCompletion' not in config or type(config['quitOnCompletion']) is bool,
        'Normal owned QA exit must be an explicit boolean opt-in.')
    require(not config.get('quitOnCompletion') or 'headfulCheckpoints' in checks,
        'Normal owned QA exit is supported only after admitted headful checkpoints.')
    seconds, tolerance = checks['seekSeconds'], checks['poseTolerance']
    require(type(seconds) is list and 2 <= len(seconds) <= 4
        and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in seconds)
        and len(set(seconds)) == len(seconds),
        'Supply two to four distinct finite UI seek times.')
    require(type(tolerance) in (int, float) and math.isfinite(tolerance) and 0 < tolerance <= 2e-4,
        'UI pose tolerance must be finite and at most 0.2 mm.')
    for key in ('rotationControl', 'aRotationControl'):
        require(key not in checks or type(checks[key]) is str and bool(checks[key].strip()),
            'A requested rotation control must be an exact nonempty native input name.')
    if checks.get('aWorkItemId'):
        _id(checks['aWorkItemId'])
    if 'headfulCheckpoints' in checks:
        _checkpoint_api().validate_options(checks['headfulCheckpoints'])
        require(not Path(checks['headfulCheckpoints']['helper']['path']).resolve().is_relative_to(
            Path(config['evidenceDirectory']).resolve()), 'The pinned headful helper cannot be inside mutable evidence.')
    evidence = Path(config['evidenceDirectory']).resolve()
    require(all(not Path(config[key]).resolve().is_relative_to(evidence)
        for key in ('inputBlend', 'seedBlend', 'numericReport', 'runtimeManifest', 'addonPackage'))
        and not Path(helper['path']).resolve().is_relative_to(evidence),
        'Immutable inputs cannot be inside this new mutable evidence directory.')
    require(all(not Path(config[key]['path']).resolve().is_relative_to(evidence)
        for key in WORKSPACE_PROOF_FIELDS), 'Workspace and completed-source pins must be outside mutable UI evidence.')
    json.dumps(config, allow_nan=False)
    return config


def validate_reports(config, numeric, physical, original_fixture):
    """Authenticate the exact seed and actual tested IDs before native lookup."""
    seed = numeric.get('qa_seed')
    require(numeric.get('schema') == 'x.validation.character-composition-native/1'
        and numeric.get('completed') is True and numeric.get('numeric_completed') is True
        and numeric.get('same_b_across_two_a_seek_and_object_motion') is True
        and numeric.get('fresh_native_associations_verified') is True
        and numeric.get('protected_prior_actions_unchanged') is True
        and numeric.get('protected_assets_unchanged') is True
        and numeric.get('fresh_workspace_cleanup') == 'historical_native_state_and_rows_restored'
        and type(seed) is dict and set(seed) == SEED_FIELDS and seed['schema'] == SEED_SCHEMA,
        'The QA seed requires the completed foreground report and explicit after-physical save receipt.')
    require(physical.get('schema') == 'x.validation.character-composition-sync/1'
        and physical.get('completed') is True
        and physical.get('physical_values_equal_foreground_and_two_a') is True
        and physical.get('actual_fbx_and_metadata_hashes_verified') is True,
        'The seed must follow successful physical two-A B publication proof.')
    require(all(_absolute(seed[key]) for key in ('seedBlend', 'workspacePath', 'physicalReport'))
        and all(_sha(seed[key]) for key in ('seedBlendSha256', 'workspaceSha256', 'physicalReportSha256', 'modelSha256')),
        'The saved seed receipt must pin exact immutable files.')
    require(type(seed['workItemIds']) is list and len(seed['workItemIds']) == 2
        and type(seed['itemIds']) is list and len(seed['itemIds']) == 2,
        'The seed must retain exactly the two independently tested prepared native rows.')
    historical_scenes = numeric['fresh_workspace_scene'].get('historical_rig_scene_names')
    require(type(historical_scenes) is list and historical_scenes
        and all(type(name) is str and name for name in historical_scenes)
        and len(set(historical_scenes)) == len(historical_scenes)
        and original_fixture['sceneName'] in historical_scenes and seed['sceneName'] not in historical_scenes,
        'The numeric proof must retain all exact historical native Rig Scene memberships.')
    expected_scenes = sorted(set(historical_scenes) | {seed['sceneName']})
    require(type(seed['rigSceneNames']) is list and seed['rigSceneNames'] == expected_scenes,
        'Keep all exact original and official copied QA Scene memberships.')
    work_ids, item_ids = [_id(value) for value in seed['workItemIds']], [_id(value) for value in seed['itemIds']]
    require(len(set(work_ids)) == len(set(item_ids)) == 2
        and work_ids == [_id(row['work_item_id']) for row in numeric['fresh_packet_proofs']]
        and work_ids == [_id(row['actualWorkItemId']) for row in physical['items'][:2]]
        and all(row.get('completed') is True and row.get('actual_fbx_and_metadata_hashes_verified') is True
            and row.get('kind') == 'SOURCE' and row.get('newActionId') == ''
            and row.get('modelSha256') == seed['modelSha256']
            for row in physical['items'][:2])
        and all(row.get('model_sha256') == seed['modelSha256'] for row in numeric['fresh_packet_proofs']),
        'Seed rows must equal the exact successful numeric and physical prepared-model variants.')
    require(_id(config['workItemId']) in work_ids
        and Path(config['seedBlend']).resolve() == Path(seed['seedBlend']).resolve()
        and config['seedBlendSha256'] == seed['seedBlendSha256']
        and config['sceneName'] == seed['sceneName'] == numeric['fresh_workspace_scene']['name']
        and config['rigName'] == seed['rigName'] == 'CoshaRig'
        and config['targetGuid'] == seed['targetGuid']
        and config['modelSha256'] == seed['modelSha256'] == original_fixture['compositionModelSha256'],
        'Configured Scene, native B ID, target or current model differs from the saved QA receipt.')
    require(Path(config['inputBlend']).resolve() == Path(original_fixture['inputBlend']).resolve()
        and config['inputBlendSha256'] == original_fixture['inputBlendSha256']
        and Path(config['addonPackage']).resolve() == Path(original_fixture['addonPackage']).resolve()
        and Path(config['runtimeManifest']).resolve() == Path(original_fixture['runtimeManifest']).resolve()
        and config['runtimeManifestSha256'] == original_fixture['runtimeManifestSha256'],
        'The UI fixture must retain the exact original saved-copy and complete runtime projection provenance.')
    return seed, item_ids[work_ids.index(_id(config['workItemId']))]


def claim_evidence(config, cache):
    """Allow startup-created empty DATAFILES directories, never old evidence."""
    directory, cache = Path(config['evidenceDirectory']).resolve(), Path(cache).resolve()
    require(cache != directory and cache.is_relative_to(directory),
        'BLENDER_USER_DATAFILES must be a dedicated child of this new UI evidence directory.')
    roots = [cache]
    if config['sessionConfig'].get('headfulCheckpoints'):
        roots = _checkpoint_api().owned_profile(directory,
            os.environ.get('BLENDER_USER_CONFIG', ''), str(cache))
    if directory.exists():
        require(directory.is_dir() and all(path.is_dir() and
            any(path == root or path.is_relative_to(root) or root.is_relative_to(path) for root in roots)
            for path in directory.rglob('*')), 'Preserve prior evidence and caches; use a new UI evidence directory.')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def select_native_row(saved, rig, work_item_id, native_item_id):
    matches = [row for row in saved.items if getattr(row, 'work_item_id', '')
        and _id(row.work_item_id) == _id(work_item_id)]
    require(len(matches) == 1 and matches[0].rig is rig and _id(matches[0].item_id) == _id(native_item_id)
        and not matches[0].new_animation, 'The actual Work Item must map to one exact saved prepared B row and native Rig.')
    return matches[0]


def _pin_model(source_module, current_link, model_file, expected_sha256, pin):
    model, digest = source_module._source_model(current_link, model_file)
    require(digest == expected_sha256, 'The actual linked model digest differs from the pinned UI fixture.')
    pin(model, expected_sha256)
    return model


def _activate_selected(context, rig, item, links, worklist, native, model_sha256, fingerprint):
    """Plain row identity survives activation; native proof resolves it fresh."""
    selected_id = str(item.item_id)
    _id(selected_id)
    anchor = native.capture_import_anchor(context, rig, selected_id, worklist, fingerprint)
    worklist.activate(context, selected_id, 'CUSTOM')
    association = native.fresh_association_proof(context, rig, selected_id, links, worklist, model_sha256,
                                                anchor=anchor, fingerprint=fingerprint)
    item = native.reacquire_work_item(context, rig, selected_id, worklist, anchor=anchor, fingerprint=fingerprint)
    return selected_id, association, item


def historical_runtime_config(config, original_fixture):
    """Keep sealed n16 runtime proof separate from the current UI candidate."""
    source_config = dict(config)
    for key in ('addonPackage', 'runtimeManifest', 'runtimeManifestSha256'):
        source_config[key] = original_fixture[key]
    return source_config


def validate_core_catalog(old, fresh, *, workspace, route_fields, protected_work_ids):
    """Core editing admits Collect changes while protecting every prepared route."""
    protected = {_id(value) for value in protected_work_ids}
    require(workspace.workspace_identity(old) == workspace.workspace_identity(fresh),
        'Core editing must retain the full target/model workspace identity.')
    prepared = [row for row in old['clips'] if row.get('linkManifest') and row.get('sourcePackage')
        and not row.get('needsRebuild')]
    require(len(protected) == 3 and protected <= {workspace.work_item_id(row) for row in prepared},
        'The core catalog must protect the two completed Pose Sources and moving Loop.')
    old_keys = {workspace.row_key(row) for row in prepared}
    require(len(old_keys) == len(prepared), 'The initial prepared routes must be unique.')
    for prior in prepared:
        matches = [row for row in fresh['clips'] if workspace.row_key(row) == workspace.row_key(prior)]
        require(len(matches) == 1 and not matches[0].get('needsRebuild')
            and workspace.route_identity(matches[0]) == workspace.route_identity(prior)
            and all(matches[0].get(field) == prior.get(field) for field in route_fields)
            and matches[0].get('composition') == prior.get('composition'),
            'An original prepared scope/ID/packet/Link/spec route changed during core Collect.')
    current_prepared = [row for row in fresh['clips'] if row.get('linkManifest') and row.get('sourcePackage')
        and not row.get('needsRebuild')]
    require(len(current_prepared) == len(prepared)
        and {workspace.row_key(row) for row in current_prepared} == old_keys,
        'Core editing cannot admit an unrelated newly prepared Source.')
    return {'workspace_identity_equal': True, 'protected_prepared_source_count': len(prepared),
        'protected_hold_work_ids': sorted(protected), 'actual_prepared_raise_work_id': None,
        'admitted_prepared_additions': [], 'scope': 'Core A+B editing; optional Raise deferred.'}


def run(config, report):
    import bpy
    options = config['sessionConfig'].get('headfulCheckpoints')
    if options:
        require(not bpy.app.background and bpy.context.window is not None,
                'Headful checkpoint admission requires a separately owned visible Blender process.')
        _checkpoint_api().owned_profile(config['evidenceDirectory'],
            os.environ.get('BLENDER_USER_CONFIG', ''), os.environ.get('BLENDER_USER_DATAFILES', ''))
    native = importlib.import_module('verify_character_composition_native')
    pinned = {}
    def pin(path, digest):
        resolved = native.pinned_file(path, digest)
        pinned[str(resolved)] = digest
        return resolved
    def pinned_json(path, digest):
        resolved = pin(path, digest)
        require(resolved.stat().st_size <= 32*1024*1024, 'The pinned proof exceeds the bounded 32 MiB reader.')
        return json.loads(resolved.read_text(encoding='utf-8'))
    numeric = pinned_json(config['numericReport'], config['numericReportSha256'])
    receipt = numeric.get('qa_seed', {})
    physical = pinned_json(receipt.get('physicalReport', ''), receipt.get('physicalReportSha256', ''))
    fixture_path = pin(numeric['fixture'], numeric['fixture_sha256'])
    original_fixture = native.validate_fixture(json.loads(fixture_path.read_text(encoding='utf-8')))
    source_config = historical_runtime_config(config, original_fixture)
    seed, native_item_id = validate_reports(source_config, numeric, physical, original_fixture)
    source_runtime = pin(source_config['runtimeManifest'], source_config['runtimeManifestSha256'])
    source = pin(config['inputBlend'], config['inputBlendSha256'])
    seed_path = pin(config['seedBlend'], config['seedBlendSha256'])
    stage_path = pin(Path(__file__).resolve().with_name('run_character_composition_raise_stage.py'), SOURCE_STAGE_SHA256)
    require(not stage_path.is_relative_to(Path(config['evidenceDirectory']).resolve()),
        'The reviewed pure Source admission helper must be outside mutable evidence.')
    stage = native.load_helper(stage_path, '_character_composition_ui_completed_source_admission')
    completed = pinned_json(config['sourceCompletionClosure']['path'], config['sourceCompletionClosure']['sha256'])
    audit = pinned_json(config['sourceCompletedFileAudit']['path'], config['sourceCompletedFileAudit']['sha256'])
    exited = pinned_json(completed['process_exit']['path'], completed['process_exit']['sha256'])
    completed_config = dict(source_config, initialWorkspaceSnapshot=config['historicalWorkspaceSnapshot'])
    report['source_completion'] = stage.validate_source_completion(
        completed_config, numeric, seed, original_fixture, completed, audit, exited)
    historical_path = pin(config['historicalWorkspaceSnapshot']['path'], config['historicalWorkspaceSnapshot']['sha256'])
    workspace_path = pin(config['currentWorkspace']['path'], config['currentWorkspace']['sha256'])
    require(workspace_path == Path(seed['workspacePath']).resolve(),
        'The pinned current catalog must be the exact completed seed workspace path.')
    helper_path = pin(config['sessionHelper']['path'], config['sessionHelper']['sha256'])
    pin(config['runtimeManifest'], config['runtimeManifestSha256'])
    _cd, report['runtime'] = native.runtime_projection(config)
    report['runtime_provenance'] = {'sealed_source_runtime': str(source_runtime),
        'sealed_source_runtime_sha256': source_config['runtimeManifestSha256'],
        'current_test_runtime': config['runtimeManifest'],
        'current_test_runtime_sha256': config['runtimeManifestSha256'],
        'historical_source_proof_is_not_current_runtime_native_acceptance': True}
    cache = Path(bpy.utils.user_resource('DATAFILES', create=True)).resolve()
    require(cache.is_relative_to(Path(config['evidenceDirectory']).resolve())
        and Path(os.environ.get('BLENDER_USER_DATAFILES', '')).resolve() == cache,
        'The isolated process must use its explicitly owned BLENDER_USER_DATAFILES cache.')
    if options:
        checkpoint_path = pin(options['helper']['path'], options['helper']['sha256'])
        require(checkpoint_path == Path(__file__).resolve().with_name('composition_ui_checkpoints.py'),
            'Use the exact canonical sibling checkpoint helper admitted by this runner.')
        checkpoints = native.load_helper(checkpoint_path, '_character_composition_headful_ui_checkpoints')
    else:
        checkpoints = _checkpoint_api()
    require(callable(getattr(checkpoints, 'owned_scene_context', None)),
        'The pinned checkpoint helper must provide the owned QA Scene context boundary.')
    require(bpy.ops.wm.open_mainfile(filepath=str(seed_path), use_scripts=False, load_ui=False) == {'FINISHED'},
        'Blender did not reopen the immutable QA seed.')
    with checkpoints.owned_scene_context(config['sceneName']):
        scene, rig = bpy.data.scenes.get(config['sceneName']), bpy.data.objects.get(config['rigName'])
        require(scene is not None and rig is not None and rig.name in scene.objects
            and sorted(owner.name for owner in rig.users_scene) == seed['rigSceneNames']
            and bpy.context.window is not None,
            'The exact original native Rig must retain both pinned original and QA Scene memberships.')
        bpy.context.window.scene = scene
        modules = {name: importlib.import_module('character_designer.'+name) for name in
            ('animation_worklist', 'animation_worklist_target', 'animation_composition',
             'animation_link', 'animation_link_source', 'animation_composition_export', 'animation_worklist_fingerprint',
            'animation_workspace')}
        worklist, target, composition, links, source_module, export, fingerprint, workspace = modules.values()
        saved = worklist.state(bpy.context)
        require(saved.rig is rig and target.is_adopted(rig) and target._load(rig)['scene'] is scene
            and saved.target_guid == config['targetGuid'] and saved.model_sha256 == config['modelSha256']
            and Path(saved.workspace_path).resolve() == workspace_path and not composition.active(rig),
            'The saved QA adoption/owner/workspace must use the actual fresh model and idle session.')
        item = select_native_row(saved, rig, config['workItemId'], native_item_id)
        catalog = worklist._fresh_workspace(saved, item)
        require(Path(catalog['_manifest_path']).resolve() == workspace_path,
            'The selected B row lost its exact current QA workspace.')
        historical = workspace.parse_workspace(historical_path.read_bytes())
        require(worklist._identity(historical) == worklist._identity(catalog) == saved.workspace_identity,
            'The current catalog must retain the exact completed native adoption and model identity.')
        protected_work_ids = [row['work_item_id'] for row in audit['publications']]
        require(len(protected_work_ids) == 3 and len({_id(value) for value in protected_work_ids}) == 3
            and {_id(value) for value in seed['workItemIds']} <= {_id(value) for value in protected_work_ids},
            'The completed audit must retain its exact two Pose Sources and original moving Loop.')
        if config['preparedRaiseWorkItemId'] is None:
            report['catalog_extension'] = validate_core_catalog(historical, catalog, workspace=workspace,
                route_fields=stage.ROUTE_FIELDS, protected_work_ids=protected_work_ids)
        else:
            report['catalog_extension'] = stage.validate_catalog_extension(historical, catalog, workspace=workspace,
                raise_work_id=config['preparedRaiseWorkItemId'], protected_work_ids=protected_work_ids)
        report['protected_pose_routes'] = []
        for pose_work_id, pose_item_id in zip(seed['workItemIds'], seed['itemIds']):
            pose = select_native_row(saved, rig, pose_work_id, pose_item_id)
            pose_clips = [row for row in catalog['clips'] if workspace.work_item_id(row) == _id(pose_work_id)]
            require(len(pose_clips) == 1, 'Each completed Pose must retain one genuine current catalog route.')
            worklist._validate_route(saved, pose_clips[0], links.load_link(pose.manifest_path), pose)
            worklist._check_baseline(pose)
            pose_spec, pose_packet = composition.item_spec(pose), composition._packet_for_item(pose)
            require(pose_spec['timeMode'] == 'HoldPose' and pose_spec['sampleEnd'] > pose_spec['sampleStart']
                and _id(pose_spec['editWorkItemId']) == _id(pose_work_id) and not pose_spec['editNewActionId'],
                'Both seeded B rows must remain actual prepared positive-duration Hold Poses.')
            pose_scope = export.resolve_scope(bpy.context, rig, pose_spec, pose_packet)
            require(len(pose_scope.mapping.indices) == 217,
                'Both completed Pose rows must prove all 217 actual native bones.')
            pin(pose_packet['_path'], pose_packet['_sha256'])
            report['protected_pose_routes'].append({'work_item_id':_id(pose_work_id), 'native_item_id':pose_item_id,
                'packet_sha256':pose_packet['_sha256'], 'native_count':len(pose_scope.mapping.indices)})
        del pose, pose_scope, pose_packet, pose_spec, pose_clips
        clip = next((row for row in catalog['clips'] if worklist._clip_key(row) == item.clip_key), None)
        require(clip is not None and not clip.get('needsRebuild') and Path(clip['linkManifest']).resolve() == Path(item.manifest_path).resolve(),
            'The actual selected B must retain its current prepared Link route.')
        current_link = links.load_link(item.manifest_path)
        worklist._validate_route(saved, clip, current_link, item)
        _pin_model(source_module, current_link, item.model_file, config['modelSha256'], pin)
        worklist._check_baseline(item)
        spec, packet = composition.item_spec(item), composition._packet_for_item(item)
        require(spec is not None and spec['timeMode'] == 'HoldPose'
            and _id(spec['editWorkItemId']) == _id(config['workItemId']) and not spec['editNewActionId'],
            'UI acceptance requires this exact genuine prepared positive-duration Hold Pose B.')
        scope = export.resolve_scope(bpy.context, rig, spec, packet)
        require(len(scope.mapping.indices) == 217, 'The UI seed must prove all 217 actual native bones.')
        for row in catalog['clips']:
            if row.get('linkManifest'):
                path = Path(row['linkManifest']).resolve()
                pin(path, native.file_hash(path))
        for row in saved.items:
            if row.source_action is not None:
                worklist._check_baseline(row)
                pin(row.source_file, row.source_hash)
        pin(packet['_path'], packet['_sha256'])
        selected_id, association, item = _activate_selected(
            bpy.context, rig, item, links, worklist, native, config['modelSha256'], fingerprint)
        report['admission'] = {'work_item_id': _id(config['workItemId']), 'native_item_id': selected_id,
            'scene_name': scene.name, 'rig_name': rig.name, 'model_sha256': config['modelSha256'],
            'packet_sha256': packet['_sha256'], 'native_count': len(scope.mapping.indices), 'association': association}
        report['immutable_files'] = dict(pinned)
        helper = native.load_helper(helper_path, '_character_composition_standalone_ui_acceptance')
        checks = dict(config['sessionConfig'], evidenceDirectory=config['evidenceDirectory'], addonModule='character_designer')
        # The helper may replace every native ID during Undo/save/reopen. Only plain
        # values survive here; no numeric fresh-QA cleanup finally is entered.
        del item, rig, scene, saved, scope, modules, packet, row
    def disk_guard():
        unchanged = all(Path(path).is_file() and native.file_hash(path) == digest
            for path, digest in pinned.items())
        report['immutable_inputs_unchanged'] = unchanged
        require(unchanged, 'An immutable seed/input/model/packet/Source/B Link or proof file changed during UI acceptance.')

    if checks.get('headfulCheckpoints'):
        options = checks['headfulCheckpoints']
        report['immutable_files'] = dict(pinned)
        require(all(callable(getattr(helper, key, None)) for key in ('run_steps', 'visual_guard', 'checkpoint_state')),
                'The pinned session helper must expose its actual generator and native protection guard.')

        def guard():
            disk_guard()
            with checkpoints.owned_scene_context(config['sceneName']):
                helper.visual_guard(bpy.context, selected_id, checks)

        def finished(session, checkpoint_report, error):
            # Only plain IDs/files survive Undo/reopen. A failed checkpoint
            # leaves the isolated draft intact and never commits or publishes.
            try:
                guard()
                if error is not None:
                    raise error
                require(session.get('complete') is True, 'Headful session acceptance did not complete.')
                report['session'] = json.loads(json.dumps(session, ensure_ascii=False, allow_nan=False))
                report.update(completed=True, source_and_b_links_unchanged=True,
                    source_link_publication_performed=False, worker_launch_performed=False, input_blend=str(source))
            except Exception as failed:
                report.update(completed=False, error_type=type(failed).__name__, error=str(failed), traceback=traceback.format_exc())
            finally:
                report['headful_checkpoints'] = checkpoint_report
                report['headful_pending'] = False
                path = Path(config['evidenceDirectory']) / 'composition_ui_run_report.json'
                path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
                if config.get('quitOnCompletion') and report.get('completed') is True:
                    checkpoints.schedule_completion_quit(bpy, report, path, guard, scene_name=config['sceneName'])

        def state_reader(checkpoint):
            with checkpoints.owned_scene_context(config['sceneName']):
                return helper.checkpoint_state(bpy.context, selected_id, checks, checkpoint)

        report['headful_pending'] = True
        with checkpoints.owned_scene_context(config['sceneName']):
            report['headful'] = checkpoints.start(bpy.context, helper.run_steps(bpy.context, selected_id, checks),
                options, config['evidenceDirectory'], finished=finished, pin_guard=guard,
                state_reader=state_reader)
        return
    try:
        session = helper.run(bpy.context, selected_id, checks)
    finally:
        # Disk-only checks retain no Blender IDs even when the helper fails or
        # reopens a file. No cleanup touches either saved Scene here.
        disk_guard()
    require(session.get('complete') is True, 'The separate UI/session helper did not complete acceptance.')
    report['session'] = json.loads(json.dumps(session, ensure_ascii=False, allow_nan=False))
    report.update(completed=True, immutable_inputs_unchanged=True, source_and_b_links_unchanged=True,
        source_link_publication_performed=False, worker_launch_performed=False, input_blend=str(source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    native = importlib.import_module('verify_character_composition_native')
    config_path = Path(args.fixture).resolve()
    config = validate_fixture(json.loads(config_path.read_text(encoding='utf-8')))
    import bpy
    cache = Path(bpy.utils.user_resource('DATAFILES', create=True)).resolve()
    directory = claim_evidence(config, cache)
    path = directory/'composition_ui_run_report.json'
    require(not path.exists(), 'Preserve the prior UI run report; use new evidence.')
    report = {'schema':REPORT_SCHEMA, 'completed':False, 'fixture':str(config_path),
        'fixture_sha256':native.file_hash(config_path), 'scope':'Immutable QA seed only; root owns the native launch.'}
    try:
        run(config, report)
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
