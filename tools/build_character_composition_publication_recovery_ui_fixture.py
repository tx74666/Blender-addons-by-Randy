"""Source-only freeze/fixture adapter for a genuinely completed publication recovery.

The old first-UI builder stays immutable. --freeze-only copies the twelve reviewed
tool sources; --write builds UI source inputs only after the fresh recovery audit,
Root closure and natural exit0. Neither action authorizes or launches a GUI.
Imports have no admission/generation side effects and never import bpy.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

ROOT = Path(r'D:/Blender/Projects/Character/X/validation/composition_20261011')
FROZEN_SCHEMA = 'x.validation.character-composition-publication-recovery-headful-tools/1'
BUILDER_SCHEMA = 'x.validation.character-composition-publication-recovery-ui-builder/1'
OLD_RECEIPT_SHA = 'f25ac72e961bbb5bc2bdd2c86432d7b807850ae4e03d948004745864bf513b1f'
RECOVERY_SHA = 'a21e531819225c4990242351ee9a2e84646cd8ac8a6714c5cd845c4a56f61fcf'
SYNC_BEFORE = '1318495399016458a913427de70b2195c983054ac72c7a0d3c1126da15c14afb'
SYNC_AFTER = 'db4d61e9deaa6833137507b49957178bc833bd5b83512fff1f809181ad49ab96'
SESSION_SHA = '47cd966e74c50188ddbebd1efe8b915dd9df645184ee7409bf657e756cee6fb3'
NATIVE_SHA = '1ebf29f7ac4358884a3861164aa0e9894db37da57f74fe64a5ef3493b650ad32'
RUNNER_SHA = '729df3152233172c0ed72f67e39801e7f2c58c4ea6be7197e04c96ab548d2b07'
FROZEN10_SHA = 'a594751f08f7266c17396a9bc104e451b2b627c7bd63e16f856347559dbd365f'
CONTEXT_SOURCES = {
    'composition_session_acceptance':'91255f9fe392a284fac332d8135e3deaa82b560b57b5a72a7e4f4eceb9dd604e',
    'run_character_composition_ui_fixture':'a88c413ab89be39bcdf675228170a6f1439734c5b818651ccffcb956ffa5ec8f',
    'composition_ui_checkpoints':'98325ddfc8c7903bc0d11f2dd78a8b62cf9457a2fc6d398ab06c6ff524a51be7',
    'run_character_composition_raise_stage':'77161b99de9d63ecc698fd00bdb1f037525c1073f9e69b7f6efc0690e1580dfc'}
OLD_NAMES = {'run_character_composition_first_stage','verify_character_composition_native',
    'composition_sync_acceptance','composition_timing_acceptance','composition_session_acceptance',
    'composition_raise_authoring','run_character_composition_raise_stage','run_character_composition_ui_fixture',
    'composition_ui_checkpoints','release_projection','run_character_composition_first_stage_continuation'}
NEW_NAME = 'run_character_composition_publication_recovery'


def require(value,message):
    if not value:
        raise ValueError(message)


def api(path,expected):
    path = Path(path).resolve()
    require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected.lower(), 'Pinned audit Source changed.')
    spec = importlib.util.spec_from_file_location('_recovery_ui_file_admission',path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def frozen09_record(guard,name,row,ledger,root):
    require(name in OLD_NAMES and type(row) is dict
            and set(row) == {'path','sha256','bytes','canonical_source'},
            'Require the exact historical frozen09 tool row format.')
    require(type(row['canonical_source']) is str and Path(row['canonical_source']).is_absolute()
            and Path(row['canonical_source']).name == name+'.py',
            'Historical frozen09 canonical provenance differs from its tool name.')
    # canonical_source is retained in the pinned old receipt as provenance; it
    # is not the executable input and may now contain later reviewed changes.
    original = guard.record({key:row[key] for key in ('path','sha256','bytes')},ledger)
    require(Path(original['path']) == root/'tools_headful_09'/(name+'.py'),
            'Old frozen source escaped its sibling directory.')
    return original


def freeze(*, audit_source,audit_source_sha256,producer_sha256,source_entry,source_entry_sha256,
           source_owner_report,source_owner_report_sha256,source_peer_report,source_peer_report_sha256,
           write=False,root=ROOT):
    guard = api(audit_source,audit_source_sha256)
    root = Path(root).resolve()
    ledger,buffers = {},{}
    producer = guard.pin(Path(__file__).resolve(),producer_sha256,ledger=ledger)
    auditor = guard.pin(audit_source,audit_source_sha256,ledger=ledger)
    tool_reviews = []
    for path,expected in ((source_owner_report,source_owner_report_sha256),(source_peer_report,source_peer_report_sha256)):
        checked = guard.pin(path,expected,ledger=ledger)
        review = guard.read(checked,ledger)
        require(review['passed'] is True and review['source_only'] is True and review['native_execution_performed'] is False
                and set(review['source_files']) == {Path(producer['path']).name,Path(auditor['path']).name},
                'Independent Source approval must cover exactly the new audit and adapter.')
        for approved in (producer,auditor):
            require(guard.record(review['source_files'][Path(approved['path']).name],ledger) == approved,
                    'A Source review approved other audit/adapter bytes.')
        tool_reviews.append(checked)
    entry_pin = guard.pin(source_entry,source_entry_sha256,ledger=ledger)
    entry = guard.read(entry_pin,ledger)
    require(Path(entry_pin['path']) == root/'n16/source_entry_receipt.json'
            and entry['schema'] == 'x.validation.character-composition-publication-recovery-source-entry/1'
            and entry['source_only'] is True and entry['native_execution_performed'] is False,
            'Freeze only actual reviewed n16 Source ancestry; no successful native claim.')
    old_pin = guard.pin(root/'tools_headful_09/frozen_tools_receipt.json',OLD_RECEIPT_SHA,ledger=ledger)
    old = guard.read(old_pin,ledger)
    require(old['schema'] == 'x.validation.character-composition-tools-frozen/1' and set(old['files']) == OLD_NAMES
            and old['files']['composition_sync_acceptance']['sha256'] == SYNC_BEFORE
            and old['files']['composition_session_acceptance']['sha256'] == SESSION_SHA
            and old['files']['verify_character_composition_native']['sha256'] == NATIVE_SHA
            and old['files']['run_character_composition_ui_fixture']['sha256'] == RUNNER_SHA,
            'Keep exact frozen09 eleven helpers and inherited EditingA witness.')
    for field in ('inherited_source_review','independent_source_review','origin_source_entry','source_manifest'):
        guard.record(old[field],ledger)
    budget_pin = guard.record(entry['sidecar_budget_projection_receipt'],ledger)
    budget = guard.read(budget_pin,ledger)
    require(budget['schema'] == 'x.validation.character-composition-animation-sidecar-budget-projections/1'
            and budget['source_only'] is True and budget['native_execution_performed'] is False
            and set(budget['fix']['changed_tool_sources']) == {'composition_sync_acceptance.py'},
            'The only changed inherited UI helper is the reviewed exact receipt-error diagnostic.')
    sync = guard.record(budget['fix']['changed_tool_sources']['composition_sync_acceptance.py'],ledger)
    require(sync['sha256'] == SYNC_AFTER, 'Use the exact reviewed Sync diagnostic, never change the native executor.')
    driver = guard.record(entry['driver'],ledger)
    require(driver['sha256'] == RECOVERY_SHA and len(entry['driver_source_reviews']) == 2,
            'Freeze the exact independently reviewed recovery driver as ancestry only.')
    reviews = []
    for row in entry['driver_source_reviews']:
        checked = guard.record(row,ledger)
        value = guard.read(checked,ledger)
        approved = guard.record(value['source_files'][NEW_NAME+'.py'],ledger)
        require(value['passed'] is True and value['source_only'] is True and value['native_execution_performed'] is False
                and approved['sha256'] == driver['sha256'] and approved['bytes'] == driver['bytes'],
                'Both owner and peer must approve the same exact recovery Source.')
        reviews.append(checked)
    for name,row in old['files'].items():
        original = frozen09_record(guard,name,row,ledger,root)
        chosen = sync if name == 'composition_sync_acceptance' else original
        buffers[name] = Path(chosen['path']).read_bytes()
    buffers[NEW_NAME] = Path(driver['path']).read_bytes()
    require(set(buffers) == OLD_NAMES|{NEW_NAME}, 'Exactly twelve reviewed tools are required.')
    # The actual BG copy keeps its unused old Sync. It must never be rewritten.
    require(entry['tools']['composition_sync_acceptance']['sha256'] == SYNC_BEFORE,
            'Preserve the actual n16 unused Sync131 ancestry independently from headful Syncdb4.')
    for name,row in entry['tools'].items():
        checked = guard.record(row,ledger)
        require(name in OLD_NAMES and checked['sha256'] == old['files'][name]['sha256'], 'A genuine n16 BG helper changed: '+name)
    destination = root/'tools_headful_10'
    require(not destination.exists(), 'Never overwrite a frozen headful toolset.')
    result = {'schema':FROZEN_SCHEMA,'source_only':True,'native_execution_performed':False,
        'origin_source_entry':entry_pin,'origin_headful_tools_receipt':old_pin,
        'recovery_driver_source_owner':reviews[0],'recovery_driver_source_peer':reviews[1],
        'source_builder':producer,'completed_file_auditor':auditor,
        'source_owner_report':tool_reviews[0],'independent_source_review':tool_reviews[1],
        'sidecar_budget_projection_receipt':budget_pin,
        'reviewed_current_overrides':[{'tool':'composition_sync_acceptance','before_sha256':SYNC_BEFORE,
            'after_sha256':SYNC_AFTER,'approved_source':sync}],
        'delta':{'changed_tools':['composition_sync_acceptance'],'added_tools':[NEW_NAME],'unchanged_count':10},
        'recovery_driver_is_source_ancestry_not_UI_executor':True,'gui_launch_permission_inferred':False,
        'generated':False,'source_inputs':ledger}
    for row in ledger.values():
        guard.record(row)
    if write:
        destination.mkdir()
        for name,raw in buffers.items():
            with (destination/(name+'.py')).open('xb') as stream:
                stream.write(raw)
        result.update(generated=True,tools={name:guard.pin(destination/(name+'.py')) for name in buffers})
        result['source_inputs'].update({row['path']:row for row in result['tools'].values()})
        guard.exclusive_json(destination/'frozen_tools_receipt.json',result)
    return result


def frozen_api(guard,row,entry_pin,ledger,root):
    receipt_pin = guard.record(row,ledger)
    require(Path(receipt_pin['path']) == root/'tools_headful_10/frozen_tools_receipt.json', 'Use the exact owned immutable frozen10 receipt.')
    receipt = guard.read(receipt_pin,ledger)
    require(receipt['schema'] == FROZEN_SCHEMA and receipt['source_only'] is True
            and receipt['native_execution_performed'] is False and receipt['generated'] is True
            and guard.same_pin(receipt['origin_source_entry'],entry_pin)
            and receipt['origin_headful_tools_receipt']['sha256'] == OLD_RECEIPT_SHA
            and receipt['recovery_driver_is_source_ancestry_not_UI_executor'] is True
            and set(receipt['tools']) == OLD_NAMES|{NEW_NAME}, 'Require the reviewed twelve-file recovery headful ancestry.')
    old = guard.read(receipt['origin_headful_tools_receipt'],ledger)
    require(old['schema'] == 'x.validation.character-composition-tools-frozen/1' and set(old['files']) == OLD_NAMES,
            'Keep the exact historical frozen09 eleven-tool receipt.')
    old_files = {name:frozen09_record(guard,name,row,ledger,root) for name,row in old['files'].items()}
    budget = guard.read(receipt['sidecar_budget_projection_receipt'],ledger)
    sync = guard.record(budget['fix']['changed_tool_sources']['composition_sync_acceptance.py'],ledger)
    require(receipt['reviewed_current_overrides'] == [{'tool':'composition_sync_acceptance','before_sha256':SYNC_BEFORE,
        'after_sha256':SYNC_AFTER,'approved_source':sync}]
        and receipt['delta'] == {'changed_tools':['composition_sync_acceptance'],'added_tools':[NEW_NAME],'unchanged_count':10},
        'No unreviewed tool override is admitted.')
    for name,row in receipt['tools'].items():
        checked = guard.record(row,ledger)
        require(Path(checked['path']) == root/'tools_headful_10'/(name+'.py')
                and checked['sha256'] == (RECOVERY_SHA if name == NEW_NAME else SYNC_AFTER
                    if name == 'composition_sync_acceptance' else old_files[name]['sha256']),
                'Frozen10 executable bytes differ from the exact old tool or sole reviewed override: '+name)
    for field in ('recovery_driver_source_owner','recovery_driver_source_peer'):
        value = guard.read(receipt[field],ledger)
        source = guard.record(value['source_files'][NEW_NAME+'.py'],ledger)
        require(value['passed'] is True and value['source_only'] is True and value['native_execution_performed'] is False
                and source['sha256'] == RECOVERY_SHA, 'Frozen recovery Source approval changed.')
    producer,auditor = [guard.record(receipt[key],ledger) for key in ('source_builder','completed_file_auditor')]
    for field in ('source_owner_report','independent_source_review'):
        review = guard.read(receipt[field],ledger)
        require(review['passed'] is True and review['source_only'] is True and review['native_execution_performed'] is False
                and all(guard.record(review['source_files'][Path(row['path']).name],ledger) == row for row in (producer,auditor)),
                'Frozen audit/adapter Source approval changed.')
    for row in receipt['source_inputs'].values():
        guard.record(row,ledger)
    return receipt_pin,receipt['tools']


def build(*,audit_source,audit_source_sha256,producer_sha256,source_entry,source_entry_sha256,completion_closure,completion_closure_sha256,
          tools_receipt,tools_receipt_sha256,legacy_builder,legacy_builder_sha256,write=False,root=ROOT):
    guard = api(audit_source,audit_source_sha256)
    root = Path(root).resolve()
    ledger = {str(Path(audit_source).resolve()):guard.pin(audit_source,audit_source_sha256)}
    producer = guard.pin(Path(__file__).resolve(),producer_sha256,ledger=ledger)
    closure_pin = guard.pin(completion_closure,completion_closure_sha256,ledger=ledger)
    closure = guard.read(closure_pin,ledger)
    explicit_entry = guard.pin(source_entry,source_entry_sha256,ledger=ledger)
    require(Path(closure_pin['path']) == root/'n16/completion_closure.json'
            and closure['schema'] == guard.CLOSURE_SCHEMA and closure['completed'] is True and closure['attempt'] == 'n16'
            and closure['natural_exit_code'] == 0 and closure['all_qa_workers_gone_verified'] is True
            and closure['qa_seed_provisional_until_this_closure'] is False
            and all(type(closure.get(key)) is int and closure[key] == value for key,value in guard.COUNTS.items()),
            'Require genuine fresh n16 recovery completion/natural exit0/Root closure, never a failed n15 closure.')
    require(guard.same_pin(closure['source_entry'],explicit_entry), 'Explicit Source entry differs from the actual Root closure.')
    audit_pin = guard.record(closure['actual_completed_file_audit'],ledger)
    completed = guard.read(audit_pin,ledger)
    require(completed['schema'] == guard.SCHEMA and completed['status'] == 'PASS' and completed['completed'] is True
            and completed['attempt'] == 'n16' and all(completed[key] == closure[key] for key in
                ('config','source_entry','python_projection','full_projection','artist','sidecar_budget_projection_receipt',
                 'process_exit','launch_receipt','pid','natural_exit_code','all_qa_workers_gone_verified','remaining_blender_processes'))
            and all(completed[key] == value for key,value in guard.COUNTS.items()), 'Root closure and exact completed file audit differ.')
    for row in completed['immutable_inputs'].values():
        guard.record(row,ledger)
    cfg,entry = [guard.read(completed[key],ledger) for key in ('config','source_entry')]
    frozen_pin,files = frozen_api(guard,{'path':tools_receipt,'sha256':tools_receipt_sha256},completed['source_entry'],ledger,root)
    frozen = guard.read(frozen_pin,ledger)
    require(frozen['source_builder'] == producer and guard.same_pin(frozen['completed_file_auditor'],ledger[str(Path(audit_source).resolve())]),
            'The actual closure/UI adapter must use the Source-reviewed audit and builder bytes.')
    legacy_pin = guard.pin(legacy_builder,legacy_builder_sha256,ledger=ledger)
    require(legacy_pin['sha256'] == guard.LEGACY_SHA,'Only the preserved exact old pure UI helpers can be reused.')
    legacy = guard.module(legacy_pin,'_recovery_UI_preserved_pure_helpers',ledger)
    native = guard.module(files['verify_character_composition_native'],'_recovery_UI_native_admission',ledger)
    runner = guard.module(files['run_character_composition_ui_fixture'],'_recovery_UI_runner_admission',ledger)
    checkpoints = guard.module(files['composition_ui_checkpoints'],'_recovery_UI_checkpoint_admission',ledger)
    fixture = native.validate_fixture(guard.read(cfg['nativeFixture'],ledger))
    numeric = guard.read(completed['reports']['numeric'],ledger)
    recovery = guard.read(completed['reports']['recovery'],ledger)
    physical = guard.read(cfg['inheritedPhysicalReport'],ledger)
    require(numeric['completed'] is True and recovery['completed'] is True
            and recovery['numeric'] == numeric and recovery['two_a_physical'] == physical,
            'Only actual completed fresh native/inner/outer recovery can produce UI inputs.')
    seed = recovery['qa_seed']
    require(seed == completed['seed_receipt'] == numeric['qa_seed'], 'The actual current seed changed after Root closure.')
    specs = [guard.read(row,ledger) for row in fixture['specFiles']]
    selected = legacy.first_hold(specs,fixture,seed)
    rotation,rotation_proof = legacy.require_ui_rotation_control(numeric,recovery['loop_authoring'],specs[0])
    workspace_pin = guard.pin(seed['workspacePath'],seed['workspaceSha256'],ledger=ledger)
    workspace = guard.read(workspace_pin,ledger)
    require(Path(fixture['compositionWorkspace']).resolve() == Path(workspace_pin['path'])
            and fixture['compositionWorkspaceSha256'] == workspace_pin['sha256']
            and workspace['targetGuid'] == seed['targetGuid'] and workspace['modelSha256'] == seed['modelSha256'],
            'The first UI must precede Parent Collect/Prepare/catalog mutation.')
    for spec,publication in zip(specs,physical['items']):
        routes = [row for row in workspace['clips'] if row.get('workItemId')
            and guard.identifier(row['workItemId']) == guard.identifier(spec['editWorkItemId'])]
        require(len(routes) == 1 and routes[0]['composition'] == spec and routes[0]['editScope'] == 'UpperBodyOverride'
                and routes[0]['needsRebuild'] is False and routes[0]['compositionReferenceSource'] is False
                and Path(routes[0]['linkManifest']).resolve() == Path(publication['manifestPath']).resolve(), 'Prepared UI SOURCE route changed.')
        route = routes[0]
        package = guard.read(guard.pin(route['sourcePackage'],route['sourcePackageSha256'],ledger=ledger),ledger)
        require(package['composition'] == spec,'The exact sealed prepared UI packet changed.')
        for pathkey,shakey in (('referencePacketFile','referencePacketSha256'),('weaponContextFile','weaponContextSha256'),('weaponModelFile','weaponModelSha256')):
            guard.pin(spec[pathkey],spec[shakey],ledger=ledger)
        companions = [row for row in workspace['clips'] if row.get('sourcePackage')
            and Path(row['sourcePackage']).resolve() == Path(spec['referencePacketFile']).resolve()
            and row.get('compositionOwnerLinkGuid') == route['linkGuid']]
        require(len(companions) == 1 and companions[0]['compositionReferenceSource'] is True
                and companions[0]['composition'] is None and companions[0]['editScope'] == 'All'
                and companions[0]['sourcePackageSha256'] == spec['referencePacketSha256'], 'Readonly A Work Copy origin changed.')
        companion = guard.read(guard.pin(companions[0]['linkManifest'],ledger=ledger),ledger)
        require(companion['compositionReferenceSource'] is True and companion['compositionOwnerLinkGuid'] == route['linkGuid']
                and companion['composition'] is None and companion['editScope'] == 'All', 'Actual readonly A companion Link changed.')
    evidence,output,receipt_output = root/'u1',root/'u1_fixture.json',root/'u1_fixture_source_receipt.json'
    require(not output.exists() and not receipt_output.exists(),'Preserve all prior fixtures; never overwrite.')
    legacy.empty_evidence(evidence)
    config = {'schema':runner.SCHEMA,**{key:fixture[key] for key in
        ('inputBlend','inputBlendSha256','addonPackage','runtimeManifest','runtimeManifestSha256')},
        'evidenceDirectory':str(evidence),'numericReport':completed['reports']['numeric']['path'],
        'numericReportSha256':completed['reports']['numeric']['sha256'],
        **{key:seed[key] for key in ('seedBlend','seedBlendSha256','sceneName','rigName','targetGuid','modelSha256')},
        'workItemId':selected,'sessionHelper':{key:files['composition_session_acceptance'][key] for key in ('path','sha256')},
        'sessionConfig':{'createAWorkCopy':True,'seekSeconds':[.137,.537],'poseTolerance':2e-4,'rotationControl':rotation,
            'headfulCheckpoints':{'schema':checkpoints.SCHEMA,'enabled':True,
                'helper':{key:files['composition_ui_checkpoints'][key] for key in ('path','sha256')},'settleSeconds':2.,'timeoutSeconds':1200.}},
        'quitOnCompletion':True}
    runner.validate_fixture(config)
    runner.validate_reports(config,numeric,physical,fixture)
    profiles = checkpoints.owned_profile(evidence,str(evidence/'CONFIG'),str(evidence/'DATAFILES'))
    units = {path.name:len(str(path).encode('utf-16-le'))//2 for path in profiles}
    require(units['DATAFILES']+174 < 260 and units['CONFIG'] < 240,'Owned UI profiles exceed Windows cache path budget.')
    require(all(not Path(row['path']).resolve().is_relative_to(evidence) for row in ledger.values()),'Immutable inputs cannot be inside mutable UI evidence.')
    for row in completed['immutable_inputs'].values():
        guard.record(row)
    for row in ledger.values():
        guard.record(row)
    result = {'schema':BUILDER_SCHEMA,'source_only':True,'native_execution_performed':False,'generated':False,
        'completion_closure':closure_pin,'actual_completed_file_audit':audit_pin,'frozen_tools_receipt':frozen_pin,
        'actual_recovery_config':completed['config'],'actual_source_entry':completed['source_entry'],
        **guard.COUNTS,'operator_proof_inherited':True,'rotation_control_binding':rotation_proof,
        'runner':files['run_character_composition_ui_fixture'],'config_path':str(output),
        'profiles':{path.name:str(path) for path in profiles},'profile_utf16_units':units,'admitted_inputs':ledger,
        'gui_grant_consumed':False,'prerequisite':'Separate fresh exact headful grant required; generate before Parent Collect/Prepare mutation. UI-edited seed is never Raise input.'}
    if write:
        guard.exclusive_json(output,config)
        evidence.mkdir(exist_ok=True)
        for path in profiles:
            path.mkdir(exist_ok=True)
        result.update(generated=True,fixture=guard.pin(output))
        guard.exclusive_json(receipt_output,result)
    return config,result


def context_sources(guard,declared,ledger):
    manifest_pin = guard.record(declared,ledger)
    manifest = guard.read(manifest_pin,ledger)
    require(manifest['schema'] == 'x.validation.character-composition-ui-owned-context-sources/1'
        and manifest['passed'] is True and manifest['source_only'] is True
        and manifest['native_execution_performed'] is False and set(manifest['tools']) == set(CONTEXT_SOURCES),
        'Require the explicit independent Source manifest for exactly four context/catalog changes.')
    chosen = {}
    for name,row in manifest['tools'].items():
        require(set(row) == {'source','owner_review','peer_review'}, 'Each changed tool needs explicit owner and peer pins.')
        source = guard.record(row['source'],ledger)
        require(Path(source['path']) == Path(__file__).resolve().with_name(name+'.py')
            and source['sha256'] == CONTEXT_SOURCES[name], 'Unreviewed current Source override: '+name)
        require(not guard.same_pin(row['owner_review'],row['peer_review']), 'Owner and independent peer must be separate evidence.')
        for key in ('owner_review','peer_review'):
            review = guard.read(guard.record(row[key],ledger),ledger)
            require(review['passed'] is True and review['source_only'] is True
                and review['native_execution_performed'] is False
                and guard.record(review['source_files'][name+'.py'],ledger) == source,
                'Source approval does not bind this exact tool: '+name)
        chosen[name] = source
    return manifest_pin,chosen


def freeze_context(*,audit_source,audit_source_sha256,producer_sha256,context_source_manifest,
                   context_source_manifest_sha256,source_owner_report,source_owner_report_sha256,
                   source_peer_report,source_peer_report_sha256,write=False,root=ROOT):
    guard = api(audit_source,audit_source_sha256)
    root,ledger = Path(root).resolve(),{}
    producer,auditor = guard.pin(__file__,producer_sha256,ledger=ledger),guard.pin(audit_source,audit_source_sha256,ledger=ledger)
    reviews = []
    for path,digest in ((source_owner_report,source_owner_report_sha256),(source_peer_report,source_peer_report_sha256)):
        row = guard.pin(path,digest,ledger=ledger)
        review = guard.read(row,ledger)
        require(review['passed'] is True and review['source_only'] is True and review['native_execution_performed'] is False
            and set(review['source_files']) == {Path(producer['path']).name,Path(auditor['path']).name}
            and all(guard.record(review['source_files'][Path(pin['path']).name],ledger) == pin for pin in (producer,auditor)),
            'Both reviews must approve exactly this adapter and unchanged completed-file auditor.')
        reviews.append(row)
    manifest,chosen = context_sources(guard,{'path':context_source_manifest,'sha256':context_source_manifest_sha256},ledger)
    old_pin = guard.pin(root/'tools_headful_10/frozen_tools_receipt.json',FROZEN10_SHA,ledger=ledger)
    old = guard.read(old_pin,ledger)
    require(old['schema'] == FROZEN_SCHEMA and old['source_only'] is True and old['native_execution_performed'] is False
        and old['generated'] is True and set(old['tools']) == OLD_NAMES|{NEW_NAME}, 'Preserve exact frozen10 twelve-tool ancestry.')
    previous_pin = guard.pin(root/'tools_headful_11/frozen_tools_receipt.json',
        '8d437212c1ad54378a078354f2f32bef5be8aaea207e42f1578e1dc5f1095cec',ledger=ledger)
    previous = guard.read(previous_pin,ledger)
    require(previous['schema'] == FROZEN_SCHEMA and previous['source_only'] is True
        and previous['native_execution_performed'] is False and previous['generated'] is True
        and previous['origin_headful_tools_receipt'] == old_pin and previous['context_source_manifest'] == manifest
        and set(previous['tools']) == OLD_NAMES|{NEW_NAME}
        and previous['delta'] == {'changed_tools':sorted(CONTEXT_SOURCES),'added_tools':[],'unchanged_count':8},
        'Use the actual immutable frozen11 ancestry, without replaying its temporal Source ledger.')
    destination = root/'tools_headful_12'
    require(not destination.exists(), 'Never overwrite existing frozen tools.')
    buffers,overrides = {},[]
    for name,row in old['tools'].items():
        original = guard.record(row,ledger)
        require(Path(original['path']) == root/'tools_headful_10'/(name+'.py'), 'Frozen10 tool escaped its directory.')
        current = chosen.get(name,original)
        buffers[name] = Path(current['path']).read_bytes()
        require(hashlib.sha256(buffers[name]).hexdigest() == current['sha256']
            and len(buffers[name]) == current['bytes'], 'Frozen input changed while reading bytes: '+name)
        previous_tool = guard.record(previous['tools'][name],ledger)
        require(Path(previous_tool['path']) == root/'tools_headful_11'/(name+'.py')
            and previous_tool['sha256'] == current['sha256'] and previous_tool['bytes'] == current['bytes'],
            'The successor must preserve every frozen11 tool byte: '+name)
        if name in chosen:
            require(current['sha256'] != original['sha256'], 'Each of the four changes must be an actual Source delta.')
            overrides.append({'tool':name,'before':original,'after':current})
    require(len(overrides) == 4 and len(buffers) == 12, 'Exactly four changed and eight unchanged tools are required.')
    result = {'schema':FROZEN_SCHEMA,'source_only':True,'native_execution_performed':False,'generated':False,
        'origin_headful_tools_receipt':old_pin,'origin_context_headful_tools_receipt':previous_pin,'context_source_manifest':manifest,
        'source_builder':producer,'completed_file_auditor':auditor,'source_owner_report':reviews[0],
        'independent_source_review':reviews[1],'reviewed_current_overrides':overrides,
        'delta':{'changed_tools':sorted(CONTEXT_SOURCES),'added_tools':[],'unchanged_count':8},
        'historical_source_ledgers_replayed':False,'gui_launch_permission_inferred':False,'source_inputs':ledger}
    for row in ledger.values():
        guard.record(row)
    if write:
        destination.mkdir()
        for name,raw in buffers.items():
            with (destination/(name+'.py')).open('xb') as stream:
                stream.write(raw)
        result.update(generated=True,tools={name:guard.pin(destination/(name+'.py')) for name in buffers})
        result['source_inputs'].update({row['path']:row for row in result['tools'].values()})
        guard.exclusive_json(destination/'frozen_tools_receipt.json',result)
    return result


@contextmanager
def pure_workspace(guard,runtime_pin,runtime,ledger):
    """Load two manifest-pinned stdlib leaves without the add-on __init__."""
    name = '_character_composition_UI_source_workspace'
    require(name not in sys.modules, 'The owned pure Source namespace already exists.')
    directory = Path(runtime_pin['path']).parent/'character_designer'
    package = types.ModuleType(name)
    package.__path__ = [str(directory)]
    sys.modules[name] = package
    try:
        for leaf in ('animation_composition_contract','animation_workspace'):
            row = guard.pin(directory/(leaf+'.py'),runtime['files'][leaf+'.py'],ledger=ledger)
            loaded = guard.module(row,name+'.'+leaf,ledger)
            setattr(package,leaf,loaded)
        yield package.animation_workspace
    finally:
        require(sys.modules.get(name) is package, 'The owned pure Source namespace was replaced.')
        del sys.modules[name]


def build_current(*,audit_source,audit_source_sha256,producer_sha256,tools_receipt,tools_receipt_sha256,
                  current_workspace,current_workspace_sha256,historical_workspace_snapshot,
                  historical_workspace_snapshot_sha256,prepared_raise_work_item_id,
                  current_runtime=None,current_runtime_sha256=None,keep_open_for_sync=False,
                  current_artist=None,current_artist_sha256=None,write=False,root=ROOT):
    require(type(keep_open_for_sync) is bool, 'Keep-open must be an explicit boolean.')
    require((current_artist is None) == (current_artist_sha256 is None), 'Current artist requires an explicit path and SHA pair.')
    guard = api(audit_source,audit_source_sha256)
    root,ledger = Path(root).resolve(),{}
    producer = guard.pin(__file__,producer_sha256,ledger=ledger)
    parent_pin = guard.pin(root/'u2_fixture.json','d9f6beae5425d2dbea55a1ab51e216f24ad367cf7019c35571e6f8254c5fcf30',ledger=ledger)
    parent_receipt_pin = guard.pin(root/'u2_fixture_source_receipt.json','17159e00f7cd3b06ba9319ee220826b9378b2b4c2e6c1f7123b5bfd6510e73ea',ledger=ledger)
    parent,parent_receipt = guard.read(parent_pin,ledger),guard.read(parent_receipt_pin,ledger)
    require(parent_receipt['fixture'] == parent_pin and parent_receipt['source_only'] is True
        and parent_receipt['native_execution_performed'] is False and parent_receipt['generated'] is True
        and parent_receipt['gui_grant_consumed'] is False and Path(parent['evidenceDirectory']) == root/'u2',
        'Use actual u2 Source ancestry only; it is not a successful UI report or reusable grant.')
    frozen_pin = guard.pin(tools_receipt,tools_receipt_sha256,ledger=ledger)
    frozen = guard.read(frozen_pin,ledger)
    previous_pin = guard.pin(root/'tools_headful_11/frozen_tools_receipt.json',
        '8d437212c1ad54378a078354f2f32bef5be8aaea207e42f1578e1dc5f1095cec',ledger=ledger)
    previous = guard.read(previous_pin,ledger)
    require(Path(frozen_pin['path']) == root/'tools_headful_12/frozen_tools_receipt.json'
        and frozen['schema'] == FROZEN_SCHEMA and frozen['source_only'] is True and frozen['native_execution_performed'] is False
        and frozen['generated'] is True and frozen['source_builder'] == producer
        and frozen['origin_headful_tools_receipt']['sha256'] == FROZEN10_SHA
        and frozen['origin_context_headful_tools_receipt'] == previous_pin
        and frozen['delta'] == {'changed_tools':sorted(CONTEXT_SOURCES),'added_tools':[],'unchanged_count':8}
        and set(frozen['tools']) == set(previous['tools']) == OLD_NAMES|{NEW_NAME},
        'Require actual reviewed frozen12 with exactly four overrides and unchanged frozen11 payloads.')
    _,chosen = context_sources(guard,frozen['context_source_manifest'],ledger)
    old = guard.read(frozen['origin_headful_tools_receipt'],ledger)
    for name,row in frozen['tools'].items():
        checked = guard.record(row,ledger)
        prior = guard.record(old['tools'][name],ledger)
        previous_tool = guard.record(previous['tools'][name],ledger)
        require(Path(checked['path']) == root/'tools_headful_12'/(name+'.py')
            and Path(previous_tool['path']) == root/'tools_headful_11'/(name+'.py')
            and checked['sha256'] == chosen.get(name,prior)['sha256'] == previous_tool['sha256']
            and checked['bytes'] == previous_tool['bytes'], 'Unreviewed frozen executable: '+name)
    for row in frozen['source_inputs'].values():
        guard.record(row,ledger)
    files = frozen['tools']
    closure_pin = guard.pin(root/'n16/completion_closure.json','9810dbf836d11a85e412a1e2632fa97026aaf37509b6de098e9af74f22ad083d',ledger=ledger)
    closure = guard.read(closure_pin,ledger)
    audit_pin = guard.record(closure['actual_completed_file_audit'],ledger)
    completed = guard.read(audit_pin,ledger)
    numeric = guard.read(completed['reports']['numeric'],ledger)
    historical_artist = dict(completed['artist'])
    if current_artist is None:
        current_artist_pin = guard.record(historical_artist,ledger)
    else:
        require(Path(current_artist).resolve() == Path(historical_artist['path']).resolve(),
            'Current artist must protect the same actual artist path as the sealed recovery audit.')
        current_artist_pin = guard.pin(current_artist,current_artist_sha256,ledger=ledger)
    source_fixture_pin = guard.pin(numeric['fixture'],numeric['fixture_sha256'],ledger=ledger)
    fixture = guard.read(source_fixture_pin,ledger)
    exited = guard.read(closure['process_exit'],ledger)
    historical_pin = guard.pin(historical_workspace_snapshot,historical_workspace_snapshot_sha256,ledger=ledger)
    current_pin = guard.pin(current_workspace,current_workspace_sha256,ledger=ledger)
    evidence,output,receipt_output = root/'u3',root/'u3_fixture.json',root/'u3_fixture_source_receipt.json'
    require(not any(path.exists() for path in (evidence,output,receipt_output)), 'Preserve all existing attempts; require new u3.')
    config = json.loads(json.dumps(parent))
    config.update(evidenceDirectory=str(evidence),sessionHelper={key:files['composition_session_acceptance'][key] for key in ('path','sha256')},
        currentWorkspace={key:current_pin[key] for key in ('path','sha256')},historicalWorkspaceSnapshot={key:historical_pin[key] for key in ('path','sha256')},
        sourceCompletionClosure={key:closure_pin[key] for key in ('path','sha256')},sourceCompletedFileAudit={key:audit_pin[key] for key in ('path','sha256')},
        preparedRaiseWorkItemId=prepared_raise_work_item_id)
    config['sessionConfig']['headfulCheckpoints']['helper'] = {key:files['composition_ui_checkpoints'][key] for key in ('path','sha256')}
    if keep_open_for_sync:
        config['quitOnCompletion'] = False
    allowed = {'evidenceDirectory','sessionHelper','sessionConfig','currentWorkspace','historicalWorkspaceSnapshot',
               'sourceCompletionClosure','sourceCompletedFileAudit','preparedRaiseWorkItemId'}
    if keep_open_for_sync:
        allowed.add('quitOnCompletion')
    require(config['quitOnCompletion'] is (False if keep_open_for_sync else parent['quitOnCompletion'])
        and all(config[key] == value for key,value in parent.items() if key not in allowed)
        and {k:v for k,v in config['sessionConfig'].items() if k != 'headfulCheckpoints'}
            == {k:v for k,v in parent['sessionConfig'].items() if k != 'headfulCheckpoints'}
        and {k:v for k,v in config['sessionConfig']['headfulCheckpoints'].items() if k != 'helper'}
            == {k:v for k,v in parent['sessionConfig']['headfulCheckpoints'].items() if k != 'helper'},
        'Only owned evidence, reviewed helper pins and explicit current-source fields may change.')
    stage = guard.module(files['run_character_composition_raise_stage'],'_current_UI_source_completion',ledger)
    runner = guard.module(files['run_character_composition_ui_fixture'],'_current_UI_fixture_validation',ledger)
    seed = numeric['qa_seed']
    historical_runtime_config = runner.historical_runtime_config(config,fixture)
    completion = stage.validate_source_completion(dict(historical_runtime_config,initialWorkspaceSnapshot=config['historicalWorkspaceSnapshot']),
        numeric,seed,fixture,closure,completed,exited)
    runtime_pin = guard.pin(historical_runtime_config['runtimeManifest'],historical_runtime_config['runtimeManifestSha256'],ledger=ledger)
    runtime = guard.read(runtime_pin,ledger)
    require(runtime_pin['sha256'] == '1479dd8dcbcbb08a27cfbb383110957f4dffd0114bc097e2c9f79ce382bc38fb', 'Use unchanged actual runtime13.')
    with pure_workspace(guard,runtime_pin,runtime,ledger) as workspace:
        historical,fresh = workspace.parse_workspace(Path(historical_pin['path']).read_bytes()),workspace.parse_workspace(Path(current_pin['path']).read_bytes())
        require(Path(current_pin['path']) == Path(seed['workspacePath']).resolve()
            and fresh['targetGuid'] == seed['targetGuid'] and fresh['modelSha256'] == seed['modelSha256'], 'Retain the actual target/model/current workspace path.')
        guard.pin(fresh['modelFile'],fresh['modelSha256'],ledger=ledger)
        protected = [row['work_item_id'] for row in completed['publications']]
        if prepared_raise_work_item_id is None:
            catalog = runner.validate_core_catalog(historical,fresh,workspace=workspace,route_fields=stage.ROUTE_FIELDS,
                protected_work_ids=protected)
        else:
            catalog = stage.validate_catalog_extension(historical,fresh,workspace=workspace,raise_work_id=prepared_raise_work_item_id,
                protected_work_ids=protected)
        for row in fresh['clips']:
            if not row.get('sourcePackage') or not row.get('linkManifest') or row.get('needsRebuild'):
                continue
            packet = guard.read(guard.pin(row['sourcePackage'],row['sourcePackageSha256'],ledger=ledger),ledger)
            link = guard.read(guard.pin(row['linkManifest'],ledger=ledger),ledger)
            require(workspace.route_identity(link) == workspace.route_identity(row)
                and link['sourcePackage'] == row['sourcePackage'] and link['sourcePackageSha256'] == row['sourcePackageSha256']
                and link['targetGuid'] == fresh['targetGuid'] and link['modelSha256'] == fresh['modelSha256']
                and guard.identifier(link['linkId']) == guard.identifier(row['linkId'])
                and packet.get('composition') == row.get('composition') == link.get('composition'), 'Current prepared Source/packet/Link identity changed.')
            if row.get('composition'):
                spec = row['composition']
                for pathkey,shakey in (('referencePacketFile','referencePacketSha256'),('weaponContextFile','weaponContextSha256'),('weaponModelFile','weaponModelSha256')):
                    guard.pin(spec[pathkey],spec[shakey],ledger=ledger)
                companions = [a for a in fresh['clips'] if a.get('compositionReferenceSource') is True
                    and a.get('compositionOwnerLinkGuid') == row['linkGuid'] and a.get('sourcePackage') == spec['referencePacketFile']]
                require(len(companions) == 1 and companions[0].get('composition') is None and companions[0]['editScope'] == 'All'
                    and companions[0]['sourcePackageSha256'] == spec['referencePacketSha256'], 'Readonly A origin must retain its exact packet and owner.')
    physical = guard.read(guard.pin(seed['physicalReport'],seed['physicalReportSha256'],ledger=ledger),ledger)
    current_runtime_pin = runtime_pin
    require((current_runtime is None) == (current_runtime_sha256 is None), 'Current runtime requires an explicit path and SHA pair.')
    if current_runtime is not None:
        current_runtime_pin = guard.pin(current_runtime,current_runtime_sha256,ledger=ledger)
        release = guard.module(files['release_projection'],'_current_UI_approved_release_projection',ledger)
        current_source = release.load_source('character_designer',current_runtime_pin['path'],root=Path(__file__).resolve().parents[1])
        require(current_source.projection_sha256 == current_runtime_pin['sha256']
            and len(current_source.files) == 167 and current_source.version == runtime['version'],
            'Use a complete approved current Character Designer projection at the same version.')
        for relative,raw in current_source.files.items():
            guard.pin(current_source.root/relative,hashlib.sha256(raw).hexdigest(),ledger=ledger)
        config.update(addonPackage=str(current_source.root),runtimeManifest=current_runtime_pin['path'],
            runtimeManifestSha256=current_runtime_pin['sha256'])
    runner.validate_fixture(config)
    runner.validate_reports(runner.historical_runtime_config(config,fixture),numeric,physical,fixture)
    for row in (completed['qa_seed'],completed['full_projection']):
        guard.record(row,ledger)
    checkpoints = guard.module(files['composition_ui_checkpoints'],'_current_UI_profiles',ledger)
    profiles = checkpoints.owned_profile(evidence,str(evidence/'CONFIG'),str(evidence/'DATAFILES'))
    units = {path.name:len(str(path).encode('utf-16-le'))//2 for path in profiles}
    require(units['DATAFILES']+174 < 260 and units['CONFIG'] < 240, 'Owned UI profiles exceed Windows cache path budget.')
    require(all(not Path(row['path']).is_relative_to(evidence) for row in ledger.values()), 'Inputs must be outside mutable evidence.')
    config_raw = (json.dumps(config,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode('utf-8')
    result = {'schema':BUILDER_SCHEMA,'source_only':True,'native_execution_performed':False,'generated':False,
        'completion_closure':closure_pin,'actual_completed_file_audit':audit_pin,'frozen_tools_receipt':frozen_pin,
        'parent_fixture':parent_pin,'parent_source_receipt':parent_receipt_pin,'source_producer':producer,
        'historical_runtime_projection':runtime_pin,'current_runtime_projection':current_runtime_pin,
        'keep_open_for_sync':keep_open_for_sync,
        'historical_artist':historical_artist,'current_artist':current_artist_pin,
        'current_artist_override_supplied':current_artist is not None,'historical_artist_is_sealed_ancestry_only':True,
        'historical_source_ancestry_only':True,'historical_input_ledgers_replayed':False,'previous_UI_success_inferred':False,
        'source_completion':completion,'catalog_extension':catalog,**guard.COUNTS,'runner':files['run_character_composition_ui_fixture'],
        'config_path':str(output),'profiles':{path.name:str(path) for path in profiles},'profile_utf16_units':units,
        'admitted_inputs':ledger,'gui_grant_consumed':False,'prerequisite':'Separate fresh Parent grant; failed u2 authorization is not reusable.'}
    for row in ledger.values():
        guard.record(row)
    if write:
        result.update(generated=True,fixture={'path':str(output),'sha256':hashlib.sha256(config_raw).hexdigest(),'bytes':len(config_raw)})
        receipt_raw = (json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode('utf-8')
        require(not any(path.exists() for path in (evidence,output,receipt_output)), 'u3 appeared before exclusive writes.')
        evidence.mkdir()
        for path in profiles:
            path.mkdir()
        with output.open('xb') as stream:
            stream.write(config_raw)
        require(guard.pin(output) == result['fixture'], 'Written config differs from encoded Source fixture.')
        with receipt_output.open('xb') as stream:
            stream.write(receipt_raw)
    return config,result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(ROOT))
    parser.add_argument('--producer-sha256',required=True)
    for name in ('audit-source','source-entry'):
        parser.add_argument('--'+name,required=name == 'audit-source')
        parser.add_argument('--'+name+'-sha256',required=name == 'audit-source')
    parser.add_argument('--freeze-only',action='store_true')
    parser.add_argument('--freeze-context',action='store_true')
    parser.add_argument('--build-current',action='store_true')
    parser.add_argument('--keep-open-for-sync',action='store_true',
        help='Only with --build-current: keep the owned QA window open after the completed session.')
    for name in ('context-source-manifest','current-workspace','historical-workspace-snapshot'):
        parser.add_argument('--'+name)
        parser.add_argument('--'+name+'-sha256')
    parser.add_argument('--prepared-raise-work-item-id')
    parser.add_argument('--current-runtime')
    parser.add_argument('--current-runtime-sha256')
    parser.add_argument('--current-artist')
    parser.add_argument('--current-artist-sha256')
    for name in ('source-owner-report','source-peer-report'):
        parser.add_argument('--'+name)
        parser.add_argument('--'+name+'-sha256')
    for name in ('completion-closure','tools-receipt','legacy-builder'):
        parser.add_argument('--'+name)
        parser.add_argument('--'+name+'-sha256')
    parser.add_argument('--write',action='store_true')
    args = parser.parse_args()
    require(sum((args.freeze_only,args.freeze_context,args.build_current)) <= 1, 'Choose exactly one source generation mode.')
    require(not args.keep_open_for_sync or args.build_current, 'Keep-open is valid only with --build-current.')
    require((args.current_artist is None) == (args.current_artist_sha256 is None), 'Current artist requires an explicit path and SHA pair.')
    require(args.current_artist is None or args.build_current, 'Current artist is valid only with --build-current.')
    if args.freeze_context:
        require(all(getattr(args,key) is not None for key in ('context_source_manifest','context_source_manifest_sha256',
            'source_owner_report','source_owner_report_sha256','source_peer_report','source_peer_report_sha256')),
            'Explicit four-source and adapter owner/peer review pins are required.')
        result = freeze_context(root=args.root,audit_source=args.audit_source,audit_source_sha256=args.audit_source_sha256,
            producer_sha256=args.producer_sha256,context_source_manifest=args.context_source_manifest,
            context_source_manifest_sha256=args.context_source_manifest_sha256,
            source_owner_report=args.source_owner_report,source_owner_report_sha256=args.source_owner_report_sha256,
            source_peer_report=args.source_peer_report,source_peer_report_sha256=args.source_peer_report_sha256,write=args.write)
    elif args.build_current:
        require(all(getattr(args,key) is not None for key in ('tools_receipt','tools_receipt_sha256','current_workspace',
            'current_workspace_sha256','historical_workspace_snapshot','historical_workspace_snapshot_sha256')),
            'Wait for actual Parent current workspace pins before build; core scope has no Raise identity.')
        _config,result = build_current(root=args.root,audit_source=args.audit_source,audit_source_sha256=args.audit_source_sha256,
            producer_sha256=args.producer_sha256,tools_receipt=args.tools_receipt,tools_receipt_sha256=args.tools_receipt_sha256,
            current_workspace=args.current_workspace,current_workspace_sha256=args.current_workspace_sha256,
            historical_workspace_snapshot=args.historical_workspace_snapshot,
            historical_workspace_snapshot_sha256=args.historical_workspace_snapshot_sha256,
            prepared_raise_work_item_id=args.prepared_raise_work_item_id,
            current_runtime=args.current_runtime,current_runtime_sha256=args.current_runtime_sha256,
            keep_open_for_sync=args.keep_open_for_sync,current_artist=args.current_artist,
            current_artist_sha256=args.current_artist_sha256,write=args.write)
    elif args.freeze_only:
        require(args.source_entry is not None and args.source_entry_sha256 is not None, 'Original source-entry pins are required.')
        require(all(getattr(args,key) is not None for key in ('source_owner_report','source_owner_report_sha256',
            'source_peer_report','source_peer_report_sha256')),'Independent Source owner/peer pins are required before freeze.')
        result = freeze(root=args.root,audit_source=args.audit_source,audit_source_sha256=args.audit_source_sha256,
            producer_sha256=args.producer_sha256,source_entry=args.source_entry,source_entry_sha256=args.source_entry_sha256,
            source_owner_report=args.source_owner_report,source_owner_report_sha256=args.source_owner_report_sha256,
            source_peer_report=args.source_peer_report,source_peer_report_sha256=args.source_peer_report_sha256,write=args.write)
    else:
        require(args.source_entry is not None and args.source_entry_sha256 is not None, 'Original source-entry pins are required.')
        require(all(getattr(args,name) is not None for name in ('completion_closure','completion_closure_sha256',
            'tools_receipt','tools_receipt_sha256','legacy_builder','legacy_builder_sha256')),'Actual closure/receipt/legacy pins are required.')
        _config,result = build(root=args.root,audit_source=args.audit_source,audit_source_sha256=args.audit_source_sha256,
            producer_sha256=args.producer_sha256,
            source_entry=args.source_entry,source_entry_sha256=args.source_entry_sha256,
            completion_closure=args.completion_closure,completion_closure_sha256=args.completion_closure_sha256,
            tools_receipt=args.tools_receipt,tools_receipt_sha256=args.tools_receipt_sha256,
            legacy_builder=args.legacy_builder,legacy_builder_sha256=args.legacy_builder_sha256,write=args.write)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))


if __name__ == '__main__':
    main()
