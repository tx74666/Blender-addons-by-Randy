"""Explicit headful UI admission for an actual completed registered NEW Raise.

Root launches this only in a fresh owned headful QA process after Raise-stage
success. Importing launches nothing. The first-stage Hold UI runner is not used
to admit this different seed, and no Sync worker/publication is invoked here.
"""
import argparse
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
import traceback
import uuid

SCHEMA = 'x.validation.character-composition-raise-ui-fixture/1'
REPORT_SCHEMA = 'x.validation.character-composition-raise-ui-run/1'
PIN_FIELDS = {'path', 'sha256'}
FIELDS = {'schema', 'driverSha256', 'raiseStageConfig', 'raiseStageReport',
          'evidenceDirectory', 'tools', 'headfulCheckpoints', 'poseTolerance'}
TOOLS = {'verify_character_composition_native', 'run_character_composition_raise_stage',
         'composition_raise_ui_acceptance', 'composition_timing_acceptance',
         'composition_session_acceptance', 'composition_ui_checkpoints', 'release_projection'}


def require(value, message):
    if not value:
        raise AssertionError(message)


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _absolute(value):
    return type(value) is str and Path(value).is_absolute() and not any(ord(char) < 32 for char in value)


def _id(value):
    require(type(value) is str, 'Use actual public/native UUID strings, never labels.')
    try:
        result = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise AssertionError('Malformed public/native UUID.') from error
    require(result.int != 0, 'The actual UUID cannot be zero.')
    return result.hex


def validate_config(config):
    require(type(config) is dict and FIELDS <= set(config) <= FIELDS | {'quitOnCompletion'} and config['schema'] == SCHEMA,
            'Use the exact explicit NEW Raise headful fixture.')
    require(_sha(config['driverSha256']) and _absolute(config['evidenceDirectory']),
            'Pin the approved runner and a fresh evidence directory.')
    require(type(config['tools']) is dict and set(config['tools']) == TOOLS,
            'Pin every reviewed native/headful helper.')
    pins = [config['raiseStageConfig'], config['raiseStageReport'], *config['tools'].values()]
    require(all(type(pin) is dict and set(pin) == PIN_FIELDS
        and _absolute(pin['path']) and _sha(pin['sha256']) for pin in pins),
        'Each immutable config/report/helper requires absolute path and exact SHA-256.')
    options = config['headfulCheckpoints']
    require(type(options) is dict and set(options) == {'schema', 'enabled', 'helper', 'settleSeconds', 'timeoutSeconds'}
        and options['schema'] == 'x.validation.character-composition-ui-checkpoints/1'
        and options['enabled'] is True and options['helper'] == config['tools']['composition_ui_checkpoints'],
        'Opt into the exact pinned actual-screen controller.')
    for key, low, high in (('settleSeconds', 1., 10.), ('timeoutSeconds', 30., 3600.)):
        require(type(options[key]) in (int, float) and math.isfinite(options[key]) and low <= options[key] <= high,
                'Use bounded finite native redraw/deadline values.')
    require(type(config['poseTolerance']) in (int, float) and math.isfinite(config['poseTolerance'])
            and 0 < config['poseTolerance'] <= 2e-4, 'Use an explicit native pose tolerance at most 0.2 mm.')
    require('quitOnCompletion' not in config or type(config['quitOnCompletion']) is bool,
            'Owned headful process completion/quit is an explicit boolean opt-in.')
    evidence = Path(config['evidenceDirectory']).resolve()
    require(all(not Path(pin['path']).resolve().is_relative_to(evidence) for pin in pins),
            'Immutable inputs/helpers cannot reside in this fresh mutable evidence directory.')
    json.dumps(config, allow_nan=False)
    return config


def validate_reports(config, stage_config, stage, numeric):
    """Pure historical receipt/UUID/clock admission before touching Blender."""
    require(stage.get('schema') == 'x.validation.character-composition-raise-stage-report/1'
        and stage.get('completed') is True
        and all(stage.get(key) is True for key in ('original_actions_unchanged',
            'original_native_state_unchanged', 'protected_original_assets_raw_exact',
            'only_actual_registered_new_manifest_published', 'immutable_inputs_unchanged'))
        and stage.get('source_links_published') is False and stage.get('artist_save_performed') is False
        and stage.get('seed_reloaded_during_execution') is False,
        'Require the actual completed protected second-stage Raise receipt.')
    require(Path(stage['config']).resolve() == Path(config['raiseStageConfig']['path']).resolve(),
            'The pinned Raise report/config pair belongs to another run.')
    output = stage.get('output_seed')
    require(type(output) is dict and set(output) == PIN_FIELDS and _absolute(output['path']) and _sha(output['sha256'])
        and stage_config.get('outputSeedFile') is not None
        and Path(stage_config['outputSeedFile']).resolve() == Path(output['path']).resolve()
        and Path(output['path']).resolve().is_relative_to(Path(stage_config['evidenceDirectory']).resolve())
        and Path(output['path']).suffix.lower() == '.blend'
        and Path(output['path']).resolve() not in {Path(stage_config['inputBlend']).resolve(), Path(stage_config['seedBlend']).resolve()},
        'The completed Raise run must save a separate immutable NEW Raise output seed.')
    require(numeric.get('schema') == 'x.validation.character-composition-native/1'
        and numeric.get('completed') is True and numeric.get('numeric_completed') is True
        and numeric.get('fresh_workspace_cleanup') == 'historical_native_state_and_rows_restored',
            'Retain the completed first-stage historical QA seed provenance.')
    seed = numeric['qa_seed']
    memberships = seed.get('rigSceneNames')
    require(type(memberships) is list and len(memberships) >= 2
        and all(type(name) is str and name for name in memberships)
        and memberships == sorted(set(memberships))
        and seed['sceneName'] in memberships, 'The actual seed lost its exact historical/QA Scene memberships.')
    require(seed['sceneName'] == stage_config['sceneName'] and seed['rigName'] == stage_config['rigName'] == 'CoshaRig'
        and seed['targetGuid'] == stage_config['targetGuid'] and seed['modelSha256'] == stage_config['modelSha256']
        and Path(seed['workspacePath']).resolve() == Path(stage_config['freshWorkspace']['path']).resolve()
        and Path(seed['seedBlend']).resolve() == Path(stage_config['seedBlend']).resolve()
        and seed['seedBlendSha256'] == stage_config['seedBlendSha256']
        and stage.get('native_count') == 217 and stage['catalog_extension']['workspace_identity_equal'] is True,
        'The actual Raise seed must retain the genuine first-stage QA owner/target/current model.')
    author, timing, physical = stage['raise_authoring'], stage['timing'], stage['physical']
    require(author.get('complete') is True and author.get('genuine_official_NewAction_registration') is True
        and author.get('protected_Customs_Sources_packets_unchanged') is True
        and author['authoring'].get('native_FK_transform_and_keys') is True
        and timing.get('complete') is True and physical.get('schema') == 'x.validation.character-composition-sync/1'
        and physical.get('completed') is True and physical.get('kind') == 'NEW'
        and physical.get('actual_fbx_and_metadata_hashes_verified') is True
        and physical.get('physical_values_equal_independent_foreground') is True,
        'Require genuine registered/moving NEW Raise, its independent clocks and actual physical publication.')
    actual = author['actual_NEW_identity']
    require(set(actual) == {'work_item_id', 'new_action_id', 'native_row_work_id', 'native_row_new_action_id',
                            'item_id', 'manifest_path', 'slot'}
        and _id(actual['work_item_id']) == actual['native_row_work_id'] == physical['actualWorkItemId']
        and _id(actual['new_action_id']) == actual['native_row_new_action_id'] == physical['newActionId']
        and actual['work_item_id'] == physical['publicManifestWorkItemId']
        and actual['new_action_id'] == physical['publicManifestNewActionId']
        and _id(actual['item_id']) and type(actual['slot']) is int and actual['slot'] >= 0
        and actual['slot'] == physical['sourceActionSlot']
        and Path(actual['manifest_path']).resolve() == Path(physical['manifestPath']).resolve(),
        'Public manifest IDs/native IDs/exact Action slot must all identify this one actual NEW Raise.')
    spec = physical['spec']
    proof = timing['raise_numeric_proof']
    require(proof['completed'] is True and proof['spec'] == spec
        and proof['provenance']['actualWorkItemId'] == actual['native_row_work_id']
        and proof['provenance']['newActionId'] == actual['native_row_new_action_id']
        and proof['effective_values'] == physical['effective_values']
        and proof['composition_export'] == physical['composition_export']
        and spec['timeMode'] == 'Raise' and spec['editWorkItemId'] == actual['work_item_id']
        and spec['editNewActionId'] == actual['new_action_id']
        and _id(spec['holdWorkItemId']) and not spec['holdNewActionId'],
        'The saved actual NEW Raise must equal its independently measured published contents and SOURCE Loop destination.')
    for field, expected in (('editSpeed', 1.7), ('editPhase', .17), ('referenceSpeed', .8), ('referencePhase', .07)):
        require(type(spec[field]) in (int, float) and math.isfinite(spec[field]) and abs(spec[field] - expected) <= 1e-7,
                'The actual sealed second-window Raise clock changed: ' + field)
    require(all(_sha(physical[key]) for key in ('manifest_after_sha256', 'fbx_sha256', 'metadata_sha256'))
        and all(_absolute(physical[key]) for key in ('manifestPath', 'fbx_file', 'metadata_file'))
        and type(physical['revision']) is int and physical['revision'] > 0,
        'Pin the completed NEW manifest/revision/FBX/metadata bytes, not its pre-publication manifest hash.')
    require(spec == physical['receipt']['composition_metadata']['composition'],
            'The native publication receipt carries a different actual Raise spec.')
    return {'sceneName': seed['sceneName'], 'rigName': seed['rigName'], 'rigSceneNames': seed['rigSceneNames'],
        'targetGuid': seed['targetGuid'], 'modelSha256': seed['modelSha256'],
        'publicWorkItemId': actual['work_item_id'], 'publicNewActionId': actual['new_action_id'],
        'nativeWorkItemId': actual['native_row_work_id'], 'nativeNewActionId': actual['native_row_new_action_id'],
        'nativeItemId': actual['item_id'], 'nativeHoldWorkItemId': _id(spec['holdWorkItemId']),
        'slot': actual['slot'], 'spec': spec, 'seed': output, 'physical': physical,
        'workspacePath': str(Path(stage_config['freshWorkspace']['path']).resolve())}


def claim_evidence(directory, roots):
    directory = Path(directory).resolve()
    if directory.exists():
        require(directory.is_dir() and all(path.is_dir() and any(path == root or path.is_relative_to(root)
            or root.is_relative_to(path) for root in roots) for path in directory.rglob('*')),
            'Use a fresh headful evidence directory; retain prior screenshots/reports/saves.')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def validate_following_publication(spec, timing, author_config):
    """Keep the actual collected SOURCE Loop revision bound to the stage proof."""
    publication = author_config['followingHoldPublication']
    keys = {'manifestPath', 'manifestSha256', 'revision', 'fbxPath',
            'fbxSha256', 'metadataPath', 'metadataSha256'}
    require(type(publication) is dict and set(publication) == keys
            and type(publication['revision']) is int and publication['revision'] > 0
            and all(_absolute(publication[key]) for key in ('manifestPath', 'fbxPath', 'metadataPath'))
            and all(_sha(publication[key]) for key in ('manifestSha256', 'fbxSha256', 'metadataSha256')),
            'The actual collected following Loop needs its exact positive physical publication pins.')
    config = timing['config']
    require(publication == config['followingHoldPublication']
            and _id(author_config['collectedHoldLoopWorkItemId']) == _id(spec['holdWorkItemId'])
            == _id(config['followingHoldWorkItemId']) == _id(config['holdLoopWorkItemId'])
            and _id(config['raiseWorkItemId']) == _id(spec['editWorkItemId']),
            'The current Raise follows another SOURCE Loop or publication from its proven second-stage timing.')
    phases = [entry for entry in timing['phases'] if entry.get('phase') == 'prove_actual_collected_following_Hold']
    require(len(phases) == 1 and phases[0].get('complete_numeric_payload_equal') is True
            and phases[0].get('no_unsynced_local_Hold_substitution') is True
            and phases[0].get('revision') == publication['revision']
            and all(phases[0].get(report_key) == publication[pin_key] for report_key, pin_key in (
                ('manifest_sha256', 'manifestSha256'), ('fbx_sha256', 'fbxSha256'),
                ('metadata_sha256', 'metadataSha256'))),
            'The second-stage timing did not prove this exact collected Loop physical revision and complete values.')
    return publication


def run(config, report):
    """Schedule the admitted headful process; no implicit process/worker launch."""
    import bpy
    require(not bpy.app.background and bpy.context.window is not None,
            'Root must explicitly launch this tool in its owned headful QA window.')
    config = validate_config(config)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    native = importlib.import_module('verify_character_composition_native')
    pinned = {}
    def pin(path, digest):
        result = native.pinned_file(path, digest)
        pinned[str(result)] = digest
        return result
    def read(value):
        result = pin(value['path'], value['sha256'])
        require(result.stat().st_size <= 32 * 1024 * 1024, 'Use bounded immutable proof/config JSON.')
        return json.loads(result.read_text(encoding='utf-8'))
    pin(__file__, config['driverSha256'])
    for name, value in config['tools'].items():
        require(pin(value['path'], value['sha256']) == Path(__file__).resolve().with_name(name + '.py'),
                'Use the exact canonical pinned sibling: ' + name)
    stage_api = importlib.import_module('run_character_composition_raise_stage')
    config_path = pin(config['raiseStageConfig']['path'], config['raiseStageConfig']['sha256'])
    stage_config = stage_api.validate_config(json.loads(config_path.read_text(encoding='utf-8')))
    stage = read(config['raiseStageReport'])
    numeric = read({'path': stage_config['numericReport'], 'sha256': stage_config['numericReportSha256']})
    admission = validate_reports(config, stage_config, stage, numeric)
    author_config = read(stage_config['raiseAuthorFixture'])
    following = validate_following_publication(admission['spec'], stage['timing'], author_config)
    for path_key, sha_key in (('manifestPath', 'manifestSha256'), ('fbxPath', 'fbxSha256'),
                              ('metadataPath', 'metadataSha256')):
        pin(following[path_key], following[sha_key])
    report['following_Hold_publication'] = following
    source = pin(stage_config['inputBlend'], stage_config['inputBlendSha256'])
    pin(stage_config['seedBlend'], stage_config['seedBlendSha256'])
    seed = pin(admission['seed']['path'], admission['seed']['sha256'])
    workspace = pin(stage_config['freshWorkspace']['path'], stage_config['freshWorkspace']['sha256'])
    pin(stage_config['runtimeManifest'], stage_config['runtimeManifestSha256'])
    _cd, report['runtime'] = native.runtime_projection(stage_config)
    checkpoints = importlib.import_module('composition_ui_checkpoints')
    roots = checkpoints.owned_profile(config['evidenceDirectory'], os.environ.get('BLENDER_USER_CONFIG', ''),
                                      os.environ.get('BLENDER_USER_DATAFILES', ''))
    require(Path(bpy.utils.user_resource('DATAFILES', create=True)).resolve() == roots[1],
            'The actual headful cache must resolve to its explicitly owned DATAFILES child.')
    directory = claim_evidence(config['evidenceDirectory'], roots)
    path = directory / 'composition_raise_ui_report.json'
    require(not path.exists(), 'Retain the prior headful report.')
    physical = admission['physical']
    pin(physical['manifestPath'], physical['manifest_after_sha256'])
    pin(physical['fbx_file'], physical['fbx_sha256'])
    metadata = read({'path': physical['metadata_file'], 'sha256': physical['metadata_sha256']})
    require(bpy.ops.wm.open_mainfile(filepath=str(seed), use_scripts=False, load_ui=False) == {'FINISHED'},
            'The immutable actual NEW Raise output seed did not reopen.')
    scene, rig = bpy.data.scenes.get(admission['sceneName']), bpy.data.objects.get(admission['rigName'])
    require(scene is not None and rig is not None and rig.name in scene.objects
        and sorted(owner.name for owner in rig.users_scene) == admission['rigSceneNames'],
        'The actual original/QA Rig membership changed in the NEW Raise seed.')
    bpy.context.window.scene = scene
    helper = importlib.import_module('composition_raise_ui_acceptance')
    session_helper = importlib.import_module('composition_session_acceptance')
    timing = importlib.import_module('composition_timing_acceptance')
    modules = helper._modules()
    workspace_api = importlib.import_module('character_designer.animation_workspace')
    admission['workspaceIdentity'] = modules['animation_worklist']._identity(workspace_api.load_workspace(workspace))
    context, saved, item = helper._current(admission['nativeItemId'], admission, modules)
    backend, worklist, new, target, export = (modules[name] for name in (
        'animation_composition', 'animation_worklist', 'animation_worklist_new',
        'animation_worklist_target', 'animation_composition_export'))
    require(not backend.active(rig) and target._load(rig)['scene'] is scene
        and saved.target_guid == admission['targetGuid'] and saved.model_sha256 == admission['modelSha256']
        and Path(saved.workspace_path).resolve() == workspace,
        'The actual saved NEW Raise requires its idle exact QA adoption/workspace.')
    target.ensure_active(context, rig)
    catalog = worklist._fresh_workspace(saved, item)
    record = new.validate(saved, item, catalog)
    require(record['workItemId'] == admission['publicWorkItemId']
        and record['newActionId'] == admission['publicNewActionId']
        and record['revision'] == physical['revision']
        and Path(record['fbxFile']).resolve() == Path(physical['fbx_file']).resolve()
        and record['fbxSha256'] == physical['fbx_sha256']
        and Path(record['metadataFile']).resolve() == Path(physical['metadata_file']).resolve()
        and record['metadataSha256'] == physical['metadata_sha256']
        and json.loads(item.last_synced_receipt) == physical['receipt'],
        'The native NEW row/manifest/publication receipt is not the completed physical Raise revision.')
    packet = backend._packet_for_item(item)
    new.authenticate_composition_bind_template(context, rig, admission['spec'], packet)
    scope = export.resolve_scope(context, rig, admission['spec'], packet)
    require(len(scope.mapping.indices) == 217 and backend.item_spec(item) == admission['spec'],
            'The actual current model/217 bones/effective control scope differ.')
    export.validate_export_receipt(metadata, admission['spec'], physical['composition_export'], physical['effective_values'])
    require(metadata['effective_values'] == physical['effective_values'], 'The actual sidecar numeric values changed.')
    content = export.effective_fingerprint(item.custom_action, item.custom_slot, scope, admission['spec'])
    receipt = physical['receipt']['proof']
    require(content['fingerprint_known'] and all(content[key] == receipt[key] for key in (
        'schema', 'fingerprint_known', 'authored_b_sha256', 'effective_input_sha256', 'fingerprint')),
        'The saved actual NEW B Custom no longer equals its published authored/effective proof.')
    for row in saved.items:
        if row.source_action is not None:
            worklist._check_baseline(row)
            pin(row.source_file, row.source_hash)
        if row.manifest_path:
            pin(row.manifest_path, native.file_hash(row.manifest_path))
        if backend.item_spec(row) is not None:
            row_packet = backend._packet_for_item(row)
            pin(row_packet['_path'], row_packet['_sha256'])
    pin(item.model_file, admission['modelSha256'])
    original_fixture = read({'path': numeric['fixture'], 'sha256': numeric['fixture_sha256']})
    preservation_pin = original_fixture['preservationHelper']
    preservation = native.load_helper(pin(preservation_pin['path'], preservation_pin['sha256']),
                                      '_composition_raise_ui_asset_auditor')
    initial_actions = helper.action_proofs(modules)
    rest = {'motion': modules['unity_animation']._rest_state(rig.data),
            'original': modules['unity_animation']._rest_state(target._load(rig)['original_data'])}
    report.update(admission=json.loads(json.dumps(admission, allow_nan=False)),
        immutable_files=dict(pinned), headful_pending=True, input_blend=str(source))
    # Keep only plain IDs/proofs through the controller's real save/reopen.
    item_id = admission['nativeItemId']
    del context, saved, item, scope, record, packet, rig, scene, catalog, row, row_packet
    def guard():
        require(all(Path(file).is_file() and native.file_hash(file) == digest for file, digest in pinned.items()),
                'An immutable seed/Source/packet/workspace/manifest/physical artifact/helper changed.')
        projection = importlib.import_module('release_projection').load_source('character_designer',
            Path(stage_config['runtimeManifest']))
        require(projection.root == Path(stage_config['addonPackage']).resolve()
                and projection.version == '0.77.25' and len(projection.files) == 167
                and projection.projection_sha256 == stage_config['runtimeManifestSha256'],
                'The complete approved runtime files/root/version changed during headful review.')
        helper.guard(item_id, admission, modules, session_helper, initial_actions, rest)
        report['immutable_inputs_unchanged'] = True
    def finished(session, checkpoint_report, error):
        try:
            guard()
            if error is not None:
                raise error
            require(session.get('complete') is True and checkpoint_report.get('raise_visual_pending') is False,
                    'Actual NEW Raise screens/save/reopen did not complete and receive acknowledgement.')
            report.update(completed=True, session=session, worker_launch_performed=False,
                source_link_publication_performed=False, artist_save_performed=False)
        except Exception as failed:
            report.update(completed=False, error=str(failed), error_type=type(failed).__name__, traceback=traceback.format_exc())
        finally:
            report.update(headful_pending=False, headful_checkpoints=checkpoint_report)
            path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
            if config.get('quitOnCompletion') and report.get('completed') is True:
                checkpoints.schedule_completion_quit(bpy, report, path, guard)
    def state_reader(checkpoint):
        return session_helper.checkpoint_state(bpy.context, item_id,
            {'addonModule': 'character_designer'}, checkpoint)
    report['headful'] = checkpoints.start(bpy.context,
        helper.run_steps(item_id, admission, config, session_helper, timing, preservation),
        config['headfulCheckpoints'], str(directory), finished=finished, pin_guard=guard, state_reader=state_reader)
    path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:])
    path = Path(args.fixture).resolve()
    config = validate_config(json.loads(path.read_text(encoding='utf-8')))
    report = {'schema': REPORT_SCHEMA, 'completed': False, 'fixture': str(path)}
    try:
        run(config, report)
    except Exception as error:
        report.update(error=str(error), error_type=type(error).__name__, traceback=traceback.format_exc())
        output = Path(config['evidenceDirectory']).resolve() / 'composition_raise_ui_admission_failure.json'
        if output.parent.exists() and not output.exists():
            output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
