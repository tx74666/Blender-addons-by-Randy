"""Root-launched native acceptance on a pinned disposable saved-artist copy.

The default entry launches no process, publishes nothing and saves no .blend.
An explicit callback can publish/then save a separate QA seed, without reload.
Run only after the root's fresh-window memory/serial checks, with --fixture.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback
import uuid

SCHEMA = 'x.validation.character-composition-fixture/1'
REPORT_SCHEMA = 'x.validation.character-composition-native/1'
SEED_SCHEMA = 'x.validation.character-composition-qa-seed/1'
SAVED_ARTIST_COPY_SHA256 = 'e8d3013ef14e02ed76160a838e7d255490fd1b6ece340e7c3f9e33dc57a3377f'
FIELDS = {'schema', 'inputBlend', 'inputBlendSha256', 'addonPackage',
          'runtimeManifest', 'runtimeManifestSha256', 'evidenceDirectory',
          'compositionWorkspace', 'compositionWorkspaceSha256', 'bWorkItemIds',
          'historicalModelSha256', 'compositionModelSha256',
          'sceneName', 'rigName', 'specFiles', 'seekSeconds', 'objectMotionCases',
          'expectedNativeCount', 'preservationHelper', 'sessionChecks'}
REFERENCE_FIELDS = {'baseClipGuid', 'baseClipLocalId', 'baseClipSha256',
                    'referencePacketFile', 'referencePacketSha256',
                    'referenceSpeed', 'referencePhase'}
VARIANT_IDENTIFIERS = {'compositionId', 'editWorkItemId', 'editNewActionId'}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def file_hash(path):
    with Path(path).open('rb') as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def pinned_file(path, expected):
    path = Path(path).resolve()
    require(path.is_file() and isinstance(expected, str) and len(expected) == 64
            and file_hash(path) == expected, 'Pinned fixture file changed: ' + str(path))
    return path


def validate_variant_specs(specs, work_ids):
    """Compare genuine B variants while authenticating per-A context copies."""
    require(len(specs) == len(work_ids) == 2, 'Two genuine variants are required.')
    contexts = [pinned_file(spec['weaponContextFile'], spec['weaponContextSha256'])
                for spec in specs]
    require(specs[0]['weaponContextSha256'] == specs[1]['weaponContextSha256']
            and contexts[0].read_bytes() == contexts[1].read_bytes(),
            'The two actual weapon context copies must have identical authenticated bytes.')
    allowed = REFERENCE_FIELDS | VARIANT_IDENTIFIERS | {'weaponContextFile'}
    require({key: value for key, value in specs[0].items() if key not in allowed}
            == {key: value for key, value in specs[1].items() if key not in allowed}
            and specs[0]['referencePacketSha256'] != specs[1]['referencePacketSha256']
            and all(uuid.UUID(spec['editWorkItemId']).hex == uuid.UUID(item_id).hex
                    for spec, item_id in zip(specs, work_ids)),
            'The genuine variant DTOs may differ only in A, real row IDs and identical pinned context copies.')


def match_qa_fk(context, rig, spec, packet):
    """Match upperbody groups before copying this isolated recovery Scene."""
    import bpy
    host = lambda name: importlib.import_module('character_designer.' + name)
    target, controls = host('animation_worklist_target'), host('animation_composition_controls')
    limb, fk = host('limb_ik'), host('limb_ik_fk')
    batch, spine = host('limb_ik_fk_batch'), host('spine_ik_fk')
    original = host('body_original_mode')
    source_copy = Path(bpy.data.filepath).resolve()
    require(source_copy.name.lower() != 'x.blend'
            and file_hash(source_copy) == SAVED_ARTIST_COPY_SHA256
            and target.managed(rig) and rig.get(target.RESTORED) is True
            and not target.is_adopted(rig)
            and not original.active(rig)
            and tuple(rig.users_scene) == (context.scene,),
            'FK preflight requires the pinned isolated copy, restored owner and one actual Scene before QA copying.')
    context.scene.tool_settings.use_keyframe_insert_auto = False
    context.view_layer.update()
    ad = rig.animation_data_create()
    require(not ad.use_tweak_mode, 'Leave native Tweak before the QA FK preflight.')
    previous_binding = {'action': ad.action.name if ad.action else None,
                        'slot': ad.action_slot.handle if ad.action_slot else 0,
                        'use_nla': bool(ad.use_nla)}
    # A historical Action may animate IK/FK switches. Detach its binding only
    # in this held recovery scope; its bytes/slot remain protected by run().
    ad.action, ad.use_nla = None, False
    context.view_layer.update()
    mapping = controls._mapping_for_scope(rig, packet, context.scene.unit_settings.scale_length)
    chosen = controls.resolve_packet_paths(spec, packet, mapping.indices)
    view = target.validation_rest_view(rig, mapping.indices)
    kwargs = {'original_rest': view, 'source_rest': view} if view is not None else {}
    inventory = limb._validate_inventory(rig, **kwargs)
    selected = tuple(key for key, record in inventory['rigs'].items()
                     if set(record['chain']) & set(chosen))
    def modes():
        return {str(key): {'mode': fk.mode_for_rig(rig, record),
                           'switch': float(rig.pose.bones[record['target'].name].get(fk.PROPERTY, 1.))}
                for key, record in inventory['rigs'].items()}
    before = modes()
    report = {'previous_binding': previous_binding, 'before': before,
              'selected_keys': [list(key) for key in selected], 'autokey': False,
              'pre_copy_scene': context.scene.name, 'actual_scene_memberships': [scene.name for scene in rig.users_scene],
              'source_copy_sha256': SAVED_ARTIST_COPY_SHA256}
    if selected:
        result = batch.switch_all(context, rig, 'FK', keys=selected, keyframe=False)
        report['limb_result'] = {**result,
            'errors': {str(key): value for key, value in result['errors'].items()}}
    spine_record = spine.validate(rig, inventory=inventory,
        **({'source_rest': view} if view is not None else {}))
    if spine_record and set(spine_record['sources']) & set(chosen):
        report['spine_before'] = spine.mode_for_rig(rig)
        report['spine_result'] = (spine.switch(context, rig, 'FK', keyframe=False)
            if report['spine_before'] != 'FK' else {'mode': 'FK', 'changed': False, 'keyed': False})
        require(spine.mode_for_rig(rig) == 'FK', 'Official QA Spine Match did not reach exact FK.')
    context.view_layer.update()
    report['after'] = modes()
    require(all(report['after'][str(key)]['mode'] == 'FK' for key in selected)
            and all(report['after'][str(key)] == before[str(key)]
                    for key in inventory['rigs'] if key not in selected),
            'Official QA Match changed an unmatched limb or did not reach exact FK.')
    scope = controls.resolve_scope(context, rig, spec, packet)
    require(len(scope.mapping.indices) == 217, 'QA FK preflight changed the native skeleton coverage.')
    report.update(full_scope_verified=True, native_count=len(scope.mapping.indices),
                  scope_proof=scope.proof, historical_recovery_owned_by_outer_scope=True)
    return report


def validate_fixture(raw):
    """Pure config admission; no Blender/Unity IO or invented prepared IDs."""
    require(type(raw) is dict and set(raw) == FIELDS and raw['schema'] == SCHEMA,
            'Incomplete or unsupported composition fixture.')
    require(raw['inputBlendSha256'] == SAVED_ARTIST_COPY_SHA256
            and Path(raw['inputBlend']).name.lower() != 'x.blend',
            'Use the verified final saved-artist validation copy, never artist X.blend.')
    require(raw['sceneName'] == 'Scene' and raw['rigName'] == 'CoshaRig'
            and raw['expectedNativeCount'] == 217,
            'This fixture is scoped to the actual original Scene/CoshaRig/217 native bones.')
    require(type(raw['specFiles']) is list and len(raw['specFiles']) == 2
            and all(type(row) is dict and set(row) == {'path', 'sha256'} for row in raw['specFiles'])
            and raw['specFiles'][0] != raw['specFiles'][1], 'Supply two distinct pinned prepared A DTOs.')
    require(type(raw['seekSeconds']) is list and 2 <= len(raw['seekSeconds']) <= 4
            and all(type(value) in (int, float) and math.isfinite(value) and value >= 0.
                    for value in raw['seekSeconds'])
            and len(set(raw['seekSeconds'])) == len(raw['seekSeconds']),
            'Supply two to four distinct finite C seek times.')
    require(type(raw['objectMotionCases']) is list and len(raw['objectMotionCases']) == 2,
            'Supply two exact disposable object Root-motion transforms.')
    for matrix in raw['objectMotionCases']:
        require(type(matrix) is list and len(matrix) == 16
                and all(type(value) in (int, float) and math.isfinite(value) for value in matrix)
                and matrix[12:16] == [0., 0., 0., 1.], 'Object-motion fixture must be finite affine.')
        columns = [[matrix[row * 4 + col] for row in range(3)] for col in range(3)]
        require(all(abs(sum(value * value for value in column) - 1.) <= 1e-8 for column in columns)
                and all(abs(sum(a * b for a, b in zip(columns[i], columns[j]))) <= 1e-8
                        for i, j in ((0, 1), (0, 2), (1, 2))),
                'Object-motion fixture may translate/rotate, never alter verified character size.')
        determinant = (matrix[0] * (matrix[5] * matrix[10] - matrix[6] * matrix[9])
            - matrix[1] * (matrix[4] * matrix[10] - matrix[6] * matrix[8])
            + matrix[2] * (matrix[4] * matrix[9] - matrix[5] * matrix[8]))
        require(abs(determinant - 1.) <= 1e-8, 'Object motion cannot mirror the source character.')
    require(raw['objectMotionCases'][0] != raw['objectMotionCases'][1], 'Object Root-motion cases must differ.')
    require(type(raw['preservationHelper']) is dict and set(raw['preservationHelper']) == {'path', 'sha256'}
            and raw['sessionChecks'] is None,
            'Use the pinned asset auditor; intentional save/reopen UI tests require a separate fixture process.')
    require(type(raw['bWorkItemIds']) is list and len(raw['bWorkItemIds']) == 2
            and len({uuid.UUID(value).hex for value in raw['bWorkItemIds']}) == 2
            and all(uuid.UUID(value).int != 0 for value in raw['bWorkItemIds']),
            'Supply two genuine prepared B variant work item IDs.')
    require(all(type(raw[field]) is str and re.fullmatch('[0-9a-f]{64}', raw[field])
                for field in ('historicalModelSha256', 'compositionModelSha256'))
            and raw['historicalModelSha256'] != raw['compositionModelSha256'],
            'Pin the distinct historical artist and actual freshly prepared model SHA-256 values.')
    return raw


def runtime_projection(config):
    package = Path(config['addonPackage']).resolve()
    require(package.name == 'character_designer' and (package / '__init__.py').is_file(),
            'Use an explicit frozen Character Designer projection.')
    manifest = pinned_file(config['runtimeManifest'], config['runtimeManifestSha256'])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from release_projection import load_source
    approved = load_source('character_designer', manifest)
    require(approved.root == package and approved.version == '0.77.25'
            and approved.projection_sha256 == config['runtimeManifestSha256']
            and len(approved.files) == 167,
            'The complete approved .25 source root/version/file inventory differs.')
    require(Path('__init__.py') in approved.files and all(Path(name) in approved.files for name in
            ('animation_composition_controls.py', 'animation_composition_export.py',
             'animation_composition_export_worker.py', 'animation_composition.py')),
            'Projection must pin the complete composition implementation.')
    sys.path.insert(0, str(package.parent))
    cd = importlib.import_module('character_designer')
    require(Path(cd.__file__).resolve().parent == package and tuple(cd.bl_info['version']) == (0, 77, 25),
            'An installed or unapproved runtime was imported.')
    cd.register()
    return cd, {'package': str(package), 'manifest': str(manifest),
                'manifest_sha256': file_hash(manifest), 'files': len(approved.files)}


def load_helper(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def action_proofs(bpy, fp):
    """Protect every currently bound/Worklist Action using exact native handles."""
    rows = {}
    def add(action, handle):
        if action is None:
            return
        key = (action.as_pointer(), handle)
        proof = fp.fingerprint_action(action, handle)
        require(proof['fingerprint_known'], 'Prior Action content is unproved: ' + action.name)
        rows[key] = (action, handle, proof)
    for obj in bpy.data.objects:
        ad = obj.animation_data
        if ad:
            add(ad.action, ad.action_slot.handle if ad.action_slot else 0)
            for track in ad.nla_tracks:
                for strip in track.strips:
                    add(strip.action, strip.action_slot_handle)
    for scene in bpy.data.scenes:
        for item in scene.character_designer_animation_worklist.items:
            add(item.source_action, item.source_slot)
            add(item.custom_action, item.custom_slot)
    return rows


def assert_actions(rows, fp):
    for action, handle, original in rows.values():
        require(fp.fingerprint_action(action, handle) == original,
                'Protected prior Action content changed: ' + action.name)


_IMPORT_ROW_FIELDS = ('item_id', 'work_item_id', 'clip_key', 'manifest_path',
                      'model_file', 'model_sha256', 'link_identity', 'export_rig_name',
                      'workspace_path', 'workspace_identity', 'source_file', 'source_hash')


def capture_import_anchor(context, rig, item_id, worklist, fingerprint):
    """Freeze the actual row and Action immediately after its genuine Add."""
    item = reacquire_work_item(context, rig, item_id, worklist)
    proof = fingerprint.fingerprint_action(item.custom_action, item.custom_slot)
    require(proof.get('fingerprint_known') is True, 'Imported B must have a complete original Action fingerprint.')
    return {'row': {key: getattr(item, key) for key in _IMPORT_ROW_FIELDS},
            'spec': json.loads(item.link_identity)['composition'],
            'custom_action': item.custom_action, 'custom_slot': item.custom_slot,
            'fingerprint': json.loads(json.dumps(proof, allow_nan=False))}


def reacquire_work_item(context, rig, item_id, worklist, *, anchor=None, fingerprint=None):
    """Collection rows are reacquired from plain IDs after every mutation."""
    require(type(item_id) is str and uuid.UUID(item_id).int != 0,
            'Retain the exact nonempty native worklist item ID before mutating its collection.')
    saved = worklist.state(context)
    require(sum(row.item_id == item_id for row in saved.items) == 1,
            'The exact native worklist item ID is missing or ambiguous.')
    item = worklist._item(saved, item_id)
    require(saved.rig is rig and item.rig is rig and item.item_id == item_id,
            'The exact native worklist item no longer belongs to this fresh Rig.')
    if anchor is not None:
        require(fingerprint is not None
                and {key: getattr(item, key) for key in _IMPORT_ROW_FIELDS} == anchor['row']
                and json.loads(item.link_identity)['composition'] == anchor['spec']
                and item.custom_action is anchor['custom_action'] and item.custom_slot == anchor['custom_slot']
                and fingerprint.fingerprint_action(item.custom_action, item.custom_slot) == anchor['fingerprint'],
                'Imported B was replaced or changed after its original per-Add anchor.')
    return item


def fresh_association_proof(context, rig, item_id, links, worklist, expected_model_sha256, *, anchor, fingerprint):
    """A native explicit activation, never a historical model marker, owns proof."""
    item = reacquire_work_item(context, rig, item_id, worklist, anchor=anchor, fingerprint=fingerprint)
    saved = worklist.state(context)
    catalog = worklist._fresh_workspace(saved, item)
    candidates = [row for row in catalog['clips'] if worklist._clip_key(row) == item.clip_key]
    require(len(candidates) == 1 and not candidates[0].get('needsRebuild')
            and not candidates[0].get('compositionReferenceSource') and not candidates[0].get('sourceOnly')
            and Path(candidates[0]['linkManifest']).resolve() == Path(item.manifest_path).resolve()
            and catalog['modelSha256'].lower() == expected_model_sha256,
            'Fresh B must retain its exact ready current-model workspace route.')
    current_link = links.load_link(item.manifest_path)
    worklist._validate_route(saved, candidates[0], current_link, item)
    worklist._check_baseline(item)
    record = links.get_link(rig)
    require(record is not None, 'Fresh B lost its explicit native rig association.')
    expected_rig_id, encoded = worklist._association_record(item)
    expected = json.loads(encoded)
    require(record['rig_id'] == expected_rig_id
            and links._identity(record['identity']) == links._identity(expected['identity'])
            and record['model_sha256'] == item.model_sha256 == expected_model_sha256
            and record['identity']['modelSha256'].lower() == expected_model_sha256
            and record['identity']['targetGuid'].lower() == saved.target_guid
            and Path(record['model_file']).resolve() == Path(item.model_file).resolve()
            and record['manifest_path'] == item.manifest_path
            and record['action_name'] == item.custom_action.name
            and record['export_rig_name'] == item.export_rig_name
            and record['action_slot'] == item.custom_slot
            and rig.get(links.ACTION_KEY) is item.custom_action
            and rig.animation_data.action is item.custom_action
            and rig.animation_data.action_slot.handle == item.custom_slot,
            'Fresh B must be explicitly activated with its exact native Action, slot, Link and current model.')
    return {'model_sha256': record['model_sha256'], 'manifest_path': record['manifest_path'],
            'packet_sha256': record['identity']['sourcePackageSha256'].lower(),
            'link_id': record['identity']['linkId'], 'action_slot': record['action_slot']}


def clear_copied_worklist(scene, original_scene, worklist):
    """Clear only the copied Scene's RNA rows, never their native Action IDs."""
    saved = scene.character_designer_animation_worklist
    require(scene is not original_scene and saved.id_data is scene,
            'Independent Worklist state must belong to the owned copied Scene.')
    for field in ('items', 'catalog', 'composition_catalog'):
        getattr(saved, field).clear()
    saved.rig = None
    for field in ('workspace_path', 'workspace_identity', 'workspace_id', 'target_guid',
                  'target_name', 'model_file', 'model_sha256', 'composition_workspace_path',
                  'composition_workspace_identity'):
        setattr(saved, field, '')
    saved.active_index = saved.catalog_index = saved.composition_catalog_index = -1
    return saved


@contextmanager
def fresh_main_rig_scene(context, rig, workspace_path, modules, report, *, before_copy=None):
    """Official disposable fresh-GUID adoption, followed by exact native recovery.

    Scene.copy preserves actual CharacterDesigner configuration/native pointers.
    All Rig/data/Action IDs remain original. No packet/GUID/owner is rewritten.
    Callbacks must not reload a .blend; the UI save/reopen fixture is separate.
    """
    import bpy
    worklist, target = modules['worklist'], modules['target']
    original, composition = modules['original'], modules['composition']
    source, export = modules['source'], modules['export']
    old_scene, old_context = context.scene, source._context_state(context)
    rig_name = rig.name
    old_rig_scene_names = sorted(scene.name for scene in rig.users_scene)
    old_saved = worklist.state(context)
    old_workspace = (old_saved.workspace_path, old_saved.workspace_identity,
                     old_saved.target_guid, old_saved.model_file, old_saved.model_sha256)
    target.ensure_active(context, rig)
    old_record = target._load(rig)
    old_metadata = target.capture_session_metadata(rig)
    current_capture = target._capture(context, rig)
    target._validate_capture(context, current_capture)
    old_properties = target._freeze(dict(rig.items()))
    old_action_refs = target._capture_action_motion_refs(old_saved.items)
    # Official Restore applies pre-adoption fake-user flags. Pin the actual
    # current flags too, including the protected original data/old bound Action.
    old_fake_users = capture_fake_users(old_record['capture'], current_capture, old_saved.items)
    old_current_state = export._artist_state(rig, old_scene)
    old_row_refs = tuple((row.item_id, row.source_action, row.source_slot,
                         row.custom_action, row.custom_slot, row.link_identity,
                         row.source_file, row.source_hash) for row in old_saved.items)
    qa = None
    try:
        # Original's embedded Dress/Body owners may reference this context.
        # Leave in the real original Scene after strict proof, before copying.
        worklist.restore_main_rig(context)
        report['original_after_historical_restore'] = original.active(rig)
        if original.active(rig):
            original._validate_session_rest(rig)
            require(original.leave(context, rig), 'Official Original leave did not complete in its owning Scene.')
        require(not original.active(rig), 'Original owner must be closed officially before copying its Scene.')
        if before_copy is not None:
            require(callable(before_copy), 'Pre-copy preparation must be an explicit held-recovery callback.')
            require(context.scene is old_scene and tuple(rig.users_scene) == (old_scene,),
                    'Official pre-copy preparation must retain the sole held original Scene owner.')
            before_copy(context)
        qa = old_scene.copy()
        qa.name = 'Composition QA · Fresh Workspace'
        context.window.scene = qa
        saved = clear_copied_worklist(qa, old_scene, worklist)
        require(qa.character_designer_setup.rig is rig and qa.objects.get(rig.name) is rig
                and context.view_layer.objects.get(rig.name) is rig,
                'Scene.copy did not retain the same configured native Rig object.')
        worklist.connect(context, str(workspace_path))
        context.view_layer.update()
        worklist.adopt_main_rig(context)
        target.ensure_active(context, rig)
        require(saved.rig is rig and target._load(rig)['scene'] is qa,
                'Fresh workspace must own its officially proven adoption in the copied Scene.')
        report['fresh_workspace_scene'] = {'name': qa.name, 'target_guid': saved.target_guid,
            'historical_target_guid': old_saved.target_guid, 'same_native_rig_object': True,
            'historical_rig_scene_names': old_rig_scene_names,
            'official_original_owner_transition': True, 'official_fresh_adoption': True}
        yield qa
    finally:
        # A reopen invalidates every held RNA reference. Never attempt recovery
        # through stale IDs; keep that intentional UI lifecycle in its own run.
        if bpy.data.objects.get(rig_name) is not rig or (qa is not None and qa not in tuple(bpy.data.scenes)):
            report['fresh_workspace_cleanup'] = 'rejected_reload_stale_native_references'
            raise AssertionError('This disposable adoption scope forbids .blend reload; use the separate UI fixture process.')
        try:
            if qa is not None:
                context.window.scene = qa
                if composition.active(rig):
                    composition.exit(context, rig, writeback=False)
                if target.is_adopted(rig) and target._load(rig)['scene'] is qa:
                    worklist.restore_main_rig(context)
            context.window.scene = old_scene
            # Restore the protected original data/display first, then its exact
            # previously active motion data/metadata, including FK properties.
            target._validate_capture(context, old_record['capture'])
            target._apply_capture(context, rig, old_record['capture'])
            target.restore_session_metadata(rig, old_metadata)
            target._validate_capture(context, current_capture)
            target._apply_capture(context, rig, current_capture)
            for key in tuple(rig.keys()):
                if key not in old_properties:
                    del rig[key]
            for key, value in old_properties.items():
                rig[key] = value
            target._restore_action_motion_refs(old_action_refs)
            for owner, value in old_fake_users:
                if owner.library is None:
                    owner.use_fake_user = value
            source._restore_context(context, old_context, playing=True)
            context.view_layer.update()
            target.ensure_active(context, rig)
            require(export._artist_state(rig, old_scene) == old_current_state
                    and old_workspace == (old_saved.workspace_path, old_saved.workspace_identity,
                        old_saved.target_guid, old_saved.model_file, old_saved.model_sha256)
                    and old_metadata == target.capture_session_metadata(rig)
                    and old_row_refs == tuple((row.item_id, row.source_action, row.source_slot,
                        row.custom_action, row.custom_slot, row.link_identity, row.source_file,
                        row.source_hash) for row in old_saved.items)
                    and all(bool(owner.use_fake_user) == value for owner, value in old_fake_users),
                    'Historical native data/Rest/FK/Action/slot/rows/session/association recovery is not exact.')
            report['fresh_workspace_cleanup'] = 'historical_native_state_and_rows_restored'
        finally:
            if qa is not None:
                bpy.data.scenes.remove(qa)


def capture_fake_users(original_capture, current_capture, items):
    """Capture flags only for the exact authenticated recovery/native row IDs."""
    owners = []
    for capture in (original_capture, current_capture):
        for entry in capture['targets']:
            owners.extend((entry['data'], entry['action']))
    for item in items:
        owners.extend((item.source_action, item.custom_action))
    unique = []
    for owner in owners:
        if owner is not None and not any(owner is previous for previous, _value in unique):
            unique.append((owner, bool(owner.use_fake_user)))
    return tuple(unique)


def phase(report, name, operation):
    started = time.perf_counter()
    print('COMPOSITION_PHASE_START ' + name, flush=True)
    try:
        return operation()
    finally:
        report.setdefault('phase_seconds', {})[name] = time.perf_counter() - started
        print('COMPOSITION_PHASE_END ' + name, flush=True)


def validate_seed_destination(filepath, numeric_report, physical_report):
    """Cheap admission before the explicit native save; no implicit worker."""
    require(type(filepath) is str and Path(filepath).is_absolute()
            and Path(filepath).suffix.lower() == '.blend' and len(filepath) < 260,
            'Save the QA seed at a dedicated absolute short .blend path.')
    require(numeric_report.get('numeric_completed') is True
            and numeric_report.get('same_b_across_two_a_seek_and_object_motion') is True
            and numeric_report.get('fresh_native_associations_verified') is True
            and physical_report.get('schema') == 'x.validation.character-composition-sync/1'
            and physical_report.get('completed') is True
            and physical_report.get('physical_values_equal_foreground_and_two_a') is True,
            'Only successful actual numeric and physical two-A proofs may produce a UI seed.')
    expected = [uuid.UUID(row['work_item_id']).hex for row in numeric_report['fresh_packet_proofs']]
    require(expected == [uuid.UUID(row['actualWorkItemId']).hex for row in physical_report['items'][:2]],
            'The physical report must name these exact independently tested prepared B variants.')
    return Path(filepath).resolve()


def save_qa_seed(context, numeric_report, filepath, *, physical_report_path):
    """Explicit callback-only save after physical proof; never reload this scope.

    The seed receipt pins the physical report and actual fresh Scene/row IDs.
    The separate UI process loads this copy and owns its intentional edits and
    save/reopen lifecycle. The numeric scope continues exact historical cleanup.
    """
    import bpy
    physical_path = Path(physical_report_path).resolve()
    require(physical_path.is_file() and physical_path.stat().st_size <= 32 * 1024 * 1024,
            'Pin the completed physical report before saving its QA seed.')
    physical = json.loads(physical_path.read_text(encoding='utf-8'))
    destination = validate_seed_destination(filepath, numeric_report, physical)
    require(destination.parent.is_dir() and not destination.exists()
            and destination != Path(bpy.data.filepath).resolve(),
            'The QA seed must be a new isolated file and cannot overwrite the opened fixture.')
    worklist = importlib.import_module('character_designer.animation_worklist')
    target = importlib.import_module('character_designer.animation_worklist_target')
    composition = importlib.import_module('character_designer.animation_composition')
    saved, scene = worklist.state(context), context.scene
    rig = saved.rig
    require(scene.name == numeric_report['fresh_workspace_scene']['name']
            and rig is not None and target._load(rig)['scene'] is scene
            and not composition.active(rig), 'Save only the current idle, officially adopted QA Scene.')
    rig_scene_names = sorted(owner.name for owner in rig.users_scene)
    require(rig_scene_names == sorted(set(numeric_report['fresh_workspace_scene']['historical_rig_scene_names']) | {scene.name}),
            'The QA copy must preserve exactly the original native Scene memberships plus its owned copy.')
    target.ensure_active(context, rig)
    work_ids = [uuid.UUID(row['work_item_id']).hex for row in numeric_report['fresh_packet_proofs']]
    items = [[row for row in saved.items if uuid.UUID(row.work_item_id).hex == item_id]
             for item_id in work_ids]
    require(all(len(rows) == 1 and rows[0].rig is rig for rows in items),
            'Each independently tested B must retain exactly one real native QA row.')
    workspace_path = Path(saved.workspace_path).resolve()
    receipt = {'schema': SEED_SCHEMA, 'seedBlend': str(destination),
        'sceneName': scene.name, 'rigName': rig.name, 'rigSceneNames': rig_scene_names,
        'targetGuid': saved.target_guid,
        'modelSha256': saved.model_sha256, 'workspacePath': str(workspace_path),
        'workspaceSha256': file_hash(workspace_path), 'workItemIds': work_ids,
        'itemIds': [rows[0].item_id for rows in items],
        'physicalReport': str(physical_path), 'physicalReportSha256': file_hash(physical_path)}
    require(bpy.ops.wm.save_as_mainfile(filepath=str(destination), copy=True) == {'FINISHED'}
            and destination.is_file() and destination.stat().st_size > 0,
            'Blender did not report a successful isolated QA seed save.')
    receipt['seedBlendSha256'] = file_hash(destination)
    numeric_report['qa_seed'] = receipt
    return receipt


def run(config, report, *, after_numeric=None, cases_runner=None):
    import bpy
    from mathutils import Matrix
    case_runner = _run_fresh_cases if cases_runner is None else cases_runner
    require(callable(case_runner), 'cases_runner is an explicit test-only continuation callable.')
    _cd, report['runtime'] = runtime_projection(config)
    source = pinned_file(config['inputBlend'], config['inputBlendSha256'])
    evidence = Path(config['evidenceDirectory']).resolve()
    cache = Path(bpy.utils.user_resource('DATAFILES', create=True)).resolve()
    require(cache.is_relative_to(evidence), 'BLENDER_USER_DATAFILES must be inside this isolated evidence directory.')
    workspace_path = pinned_file(config['compositionWorkspace'], config['compositionWorkspaceSha256'])
    helper_row = config['preservationHelper']
    preservation = load_helper(pinned_file(helper_row['path'], helper_row['sha256']), '_composition_asset_auditor')
    ua = importlib.import_module('character_designer.unity_animation')
    worklist = importlib.import_module('character_designer.animation_worklist')
    links = importlib.import_module('character_designer.animation_link')
    link_source = importlib.import_module('character_designer.animation_link_source')
    target = importlib.import_module('character_designer.animation_worklist_target')
    fp = importlib.import_module('character_designer.animation_worklist_fingerprint')
    composition = importlib.import_module('character_designer.animation_composition')
    contract = importlib.import_module('character_designer.animation_composition_contract')
    export = importlib.import_module('character_designer.animation_composition_export')
    specs = [contract.validate(json.loads(pinned_file(row['path'], row['sha256']).read_text(encoding='utf-8')))
             for row in config['specFiles']]
    validate_variant_specs(specs, config['bWorkItemIds'])
    bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False, load_ui=False)
    scene, rig = bpy.data.scenes.get(config['sceneName']), bpy.data.objects.get(config['rigName'])
    require(scene is not None and rig is not None and rig.name in scene.objects, 'Actual artist fixture target is missing.')
    bpy.context.window.scene = scene
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    rig.hide_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.context.view_layer.update()
    require(target.is_adopted(rig) and worklist.state(bpy.context).rig is rig,
            'Fixture must retain the actual final .24 adopted Main Rig transaction.')
    target.control_rest_view(rig)
    saved = worklist.state(bpy.context)
    require(saved.model_sha256 == config['historicalModelSha256'],
            'The historical recovery workspace model is not the pinned artist revision.')
    historical_workspace = (saved.workspace_path, saved.workspace_identity, saved.model_file,
                            saved.model_sha256, rig.get('character_designer_animation_source_model_sha256'))
    historical_association = {key: rig[key] for key in
        (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
    historical_session = target.capture_session_metadata(rig)
    prior_actions = action_proofs(bpy, fp)
    prior_assets = preservation.asset_snapshot(scene, rig, ua)
    modules = {'worklist': worklist, 'target': target, 'source': link_source,
               'original': importlib.import_module('character_designer.body_original_mode'),
               'composition': composition, 'export': export}
    # Authenticate the real prepared B without writing the historical Scene's
    # Library/catalog. Match while it is still the Rig's only Scene, as required
    # by the unchanged official matcher. The outer recovery scope owns all edits.
    workspace = importlib.import_module('character_designer.animation_workspace')
    actual_workspace = workspace.load_workspace(str(workspace_path))
    prepared_clips = [clip for clip in actual_workspace['clips']
        if clip.get('workItemId') and uuid.UUID(clip['workItemId']).hex == uuid.UUID(config['bWorkItemIds'][0]).hex]
    require(len(prepared_clips) == 1, 'The actual prepared B is absent before QA FK Match.')
    prepared_clip = prepared_clips[0]
    prepared_link = links.load_link(prepared_clip['linkManifest'])
    require(workspace.route_identity(prepared_clip) == workspace.route_identity(prepared_link)
            and not prepared_clip.get('needsRebuild')
            and not prepared_clip.get('sourceOnly')
            and not prepared_clip.get('compositionReferenceSource'),
            'FK preflight requires the exact genuine prepared editable B route.')
    prepared_packet, _export_name = link_source._packet(prepared_link)
    _model_path, actual_model_hash = link_source._source_model(prepared_link, actual_workspace['modelFile'])
    require(prepared_clip['composition'] == prepared_link['composition'] == prepared_packet['composition'] == specs[0]
            and actual_model_hash == config['compositionModelSha256']
            and prepared_clip['sourcePackageSha256'].lower() == prepared_packet['_sha256']
            and prepared_link['targetGuid'] == actual_workspace['targetGuid'],
            'The actual prepared B packet, DTO, target or model changed before QA FK Match.')
    def before_copy(context):
        report['qa_fk_preflight'] = phase(report, 'official_pre_copy_qa_fk_match',
            lambda: match_qa_fk(context, rig, specs[0], prepared_packet))
    with fresh_main_rig_scene(bpy.context, rig, workspace_path, modules, report, before_copy=before_copy) as qa_scene:
        controls = importlib.import_module('character_designer.animation_composition_controls')
        adopted_scope = controls.resolve_scope(bpy.context, rig, specs[0], prepared_packet)
        require(len(adopted_scope.mapping.indices) == 217,
                'Fresh adoption must retain the officially matched full native skeleton scope.')
        report['qa_fk_post_adoption'] = {'full_scope_verified': True,
            'native_count': len(adopted_scope.mapping.indices), 'scope_proof': adopted_scope.proof}
        qa_assets = preservation.asset_snapshot(qa_scene, rig, ua)
        case_runner(config, report, workspace_path, rig, qa_scene, preservation,
                         prior_actions, qa_assets, specs, after_numeric)
    assert_actions(prior_actions, fp)
    require(not preservation.changes(prior_assets, preservation.asset_snapshot(scene, rig, ua)),
            'Fresh workspace cleanup changed raw original artist assets/display/widgets.')
    require(historical_workspace == (saved.workspace_path, saved.workspace_identity, saved.model_file,
                saved.model_sha256, rig.get('character_designer_animation_source_model_sha256'))
            and historical_association == {key: rig[key] for key in
                (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
            and historical_session == target.capture_session_metadata(rig),
            'The exact historical workspace/model marker/native association/session was not restored.')
    require(file_hash(source) == config['inputBlendSha256'], 'Disposable source file was overwritten.')
    report['completed'] = True


def _run_fresh_cases(config, report, workspace_path, rig, scene, preservation, prior_actions, prior_assets, specs, after_numeric):
    import bpy
    from mathutils import Matrix
    ua = importlib.import_module('character_designer.unity_animation')
    worklist = importlib.import_module('character_designer.animation_worklist')
    links = importlib.import_module('character_designer.animation_link')
    link_source = importlib.import_module('character_designer.animation_link_source')
    target = importlib.import_module('character_designer.animation_worklist_target')
    fp = importlib.import_module('character_designer.animation_worklist_fingerprint')
    composition = importlib.import_module('character_designer.animation_composition')
    contract = importlib.import_module('character_designer.animation_composition_contract')
    export = importlib.import_module('character_designer.animation_composition_export')
    saved = worklist.state(bpy.context)
    historical_workspace = (saved.workspace_path, saved.workspace_identity, saved.model_file,
                            saved.model_sha256, rig.get('character_designer_animation_source_model_sha256'))
    historical_association = {key: rig[key] for key in
        (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
    historical_session = target.capture_session_metadata(rig)
    phase(report, 'connect_composition_library', lambda: composition.connect_library(bpy.context, str(workspace_path)))
    _library_record, library = composition._library_state(bpy.context)
    require(library.model_sha256 == config['compositionModelSha256'],
            'Composition Library does not identify the actual freshly prepared model revision.')
    item_ids, import_anchors, b_raws = [], [], []
    for index, (item_id, spec) in enumerate(zip(config['bWorkItemIds'], specs)):
        rows = [row for row in worklist.state(bpy.context).composition_catalog
                if uuid.UUID(row.work_item_id).hex == uuid.UUID(item_id).hex and row.ready]
        require(len(rows) == 1, 'Exactly one actual prepared B row is required for each variant.')
        item = phase(report, 'import_b_variant_' + str(index + 1),
                     lambda: composition.add_work_item(bpy.context, rows[0].clip_key, activate_new=False))
        native_id = str(item.item_id)
        anchor = capture_import_anchor(bpy.context, rig, native_id, worklist, fp)
        item = reacquire_work_item(bpy.context, rig, native_id, worklist, anchor=anchor, fingerprint=fp)
        import_anchors.append(anchor)
        b_raws.append(export._action_hash(item.custom_action, item.custom_slot))
        require(item.rig is rig and contract.identity(composition.item_spec(item)) == contract.identity(spec),
                'Prepared B Link differs from its independently sealed variant contract.')
        require(item.model_sha256 == config['compositionModelSha256'],
                'A historical row cannot stand in for the freshly prepared composition model.')
        fresh_packet = composition._packet_for_item(item)
        fresh_scope = export.resolve_scope(bpy.context, rig, spec, fresh_packet)
        require(len(fresh_scope.mapping.indices) == config['expectedNativeCount'],
                'The fresh packet did not prove the complete original native skeleton.')
        report.setdefault('fresh_packet_proofs', []).append({
            'work_item_id': spec['editWorkItemId'], 'model_sha256': item.model_sha256,
            'packet_sha256': fresh_packet['_sha256'], 'source_rest_sha256': spec['sourceRestSha256'],
            'original_rest_sha256': fresh_scope.proof['sourceRest'],
            'motion_rest_sha256': fresh_scope.proof['motionRest'],
            'native_count': len(fresh_scope.mapping.indices),
            'mapping_error_metres': fresh_scope.mapping.error_metres})
        item_ids.append(native_id)
    assert_actions(prior_actions, fp)
    require(not preservation.protected_preview_changes(prior_assets, preservation.asset_snapshot(scene, rig, ua)),
            'Importing B changed protected artist assets.')
    baseline_values = baseline_fingerprint = baseline_scope = None
    visible_hashes, visible_by_reference, cases = set(), {}, []
    for reference_index, (spec, item_id, b_raw, anchor) in enumerate(zip(specs, item_ids, b_raws, import_anchors)):
        label = 'a' + str(reference_index + 1)
        item = reacquire_work_item(bpy.context, rig, item_id, worklist, anchor=anchor, fingerprint=fp)
        packet = composition._packet_for_item(item)
        captured = worklist._capture_rig(bpy.context, rig)
        old_context = link_source._context_state(bpy.context)
        old_index = saved.active_index
        original_world = rig.matrix_world.copy()
        try:
            phase(report, label + '_activate_fresh_b',
                  lambda: worklist.activate(bpy.context, item_id, 'CUSTOM'))
            association = fresh_association_proof(bpy.context, rig, item_id, links, worklist,
                                                  config['compositionModelSha256'], anchor=anchor, fingerprint=fp)
            report.setdefault('fresh_associations', []).append(association)
            phase(report, label + '_enter_full_stack', lambda: composition.enter(bpy.context, spec, item_id))
            require(rig.animation_data.use_tweak_mode, 'Native Full Stack Tweak did not activate.')
            for motion_index, raw_matrix in enumerate(config['objectMotionCases']):
                delta = Matrix([raw_matrix[i:i + 4] for i in range(0, 16, 4)])
                rig.matrix_world = delta @ original_world
                for seconds in config['seekSeconds']:
                    ua._set_frame(scene, 1. + seconds * spec['fps'])
                    bpy.context.view_layer.update()
                    # Complete native inventory is resolved by the real strict
                    # scope inside Tweak without changing it to make proof pass.
                    scope = export.resolve_scope(bpy.context, rig, spec, packet)
                    visible = contract.digest({'object': [float(v) for row in rig.matrix_world for v in row],
                        'native_world': {name: [float(v) for row in (rig.matrix_world @ rig.pose.bones[name].matrix) for v in row]
                                         for name in scope.mapping.indices}})
                    visible_hashes.add(visible)
                    visible_by_reference[(reference_index, motion_index, seconds)] = visible
                    with composition.isolated_sync(bpy.context, rig) as inputs:
                        model_helpers = export.baseline_worker()._model_helpers()
                        native = tuple(sorted(b.name for b in inputs['rig'].data.bones
                            if not model_helpers._is_control(b)))
                        require(len(native) == config['expectedNativeCount']
                                and set(native) == set(inputs['scope'].mapping.indices),
                                'Copied ownership markers changed the complete native skeleton filter.')
                        values, fingerprint, proof = (inputs['effective_values'], inputs['b_fingerprint'], inputs['scope'].proof)
                        if baseline_values is None:
                            baseline_values, baseline_fingerprint, baseline_scope = values, fingerprint, proof
                        require(values == baseline_values and fingerprint == baseline_fingerprint and proof == baseline_scope,
                                'Same B changed under another A, seek or visible object Root motion.')
                        cases.append({'reference': label, 'seek_seconds': seconds, 'object_motion_case': motion_index,
                            'b_work_item_id': spec['editWorkItemId'],
                            'visible_c_sha256': visible, 'native_count': len(native),
                            'b_fingerprint': fingerprint,
                            'composition_export': inputs['composition_export']})
                    require(rig.animation_data.use_tweak_mode, 'Isolated Sync did not restore native Tweak.')
                    item = reacquire_work_item(bpy.context, rig, item_id, worklist, anchor=anchor, fingerprint=fp)
                    require(export._action_hash(item.custom_action, item.custom_slot) == b_raw,
                            'Opening/seeking/Sync manufactured B edits.')
        finally:
            rig.matrix_world = original_world
            if composition.active(rig):
                composition.exit(bpy.context, rig, writeback=False)
            worklist._restore_rig(bpy.context, rig, captured)
            link_source._restore_context(bpy.context, old_context, playing=True)
            saved.active_index = old_index
            bpy.context.view_layer.update()
        assert_actions(prior_actions, fp)
        for check_id, original, original_anchor in zip(item_ids, b_raws, import_anchors):
            row = reacquire_work_item(bpy.context, rig, check_id, worklist, anchor=original_anchor, fingerprint=fp)
            require(export._action_hash(row.custom_action, row.custom_slot) == original,
                    'Composition Exit changed original Custom B.')
        require(not preservation.protected_preview_changes(prior_assets, preservation.asset_snapshot(scene, rig, ua)),
                'Composition changed protected original assets.')
        require(historical_association == {key: rig[key] for key in
                (links.LINK_KEY, links.ACTION_KEY, links.RIG_ID_KEY) if key in rig}
                and historical_session == target.capture_session_metadata(rig)
                and historical_workspace == (saved.workspace_path, saved.workspace_identity, saved.model_file,
                    saved.model_sha256, rig.get('character_designer_animation_source_model_sha256')),
                'Fresh native association changed historical recovery workspace, packet or artist association.')
    require(len(visible_hashes) >= 2, 'The fixture never exercised a different visible C/object evaluation.')
    require(any(visible_by_reference[(0, 0, seconds)] != visible_by_reference[(1, 0, seconds)]
                for seconds in config['seekSeconds']),
            'The two actual A references never produced distinct C evaluation at matching seek/object inputs.')
    require(cases[0]['composition_export']['referenceActionBeforeSha256']
            != next(row for row in cases if row['reference'] == 'a2')['composition_export']['referenceActionBeforeSha256'],
            'The two A references have identical native authored motion; choose a meaningful independent A fixture.')
    report.update(cases=cases, effective_values=baseline_values,
                  same_b_across_two_a_seek_and_object_motion=True,
                  protected_prior_actions_unchanged=True, protected_assets_unchanged=True,
                  fresh_native_associations_verified=True, historical_recovery_association_unchanged=True,
                  worker_launch_performed=False)
    report['numeric_completed'] = True
    if after_numeric is not None:
        require(callable(after_numeric), 'after_numeric is an explicit test-only callable.')
        after_numeric(bpy.context, report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    fixture_path = Path(args.fixture).resolve()
    config = validate_fixture(json.loads(fixture_path.read_text(encoding='utf-8')))
    evidence = Path(config['evidenceDirectory']).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    report_path = evidence / 'composition_native_report.json'
    require(not report_path.exists(), 'Use a fresh isolated evidence directory; preserve prior reports.')
    report = {'schema': REPORT_SCHEMA, 'completed': False, 'fixture': str(fixture_path),
              'fixture_sha256': file_hash(fixture_path), 'scope': 'Pinned disposable copy only; no artist/Unity writes.'}
    try:
        run(config, report)
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
