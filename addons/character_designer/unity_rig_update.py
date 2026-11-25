"""Pure admission and immutable candidate transport for bounded Rig updates.

This module never imports Blender, exports a model, replaces a formal bundle,
enables receiver capabilities, or clears an accepted ledger. Admission requires
the current receiver's dedicated, hash-bound native proof. A candidate becomes
discoverable only after every payload and captured authority is checked again.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from . import unity_update_candidate as base
from . import hair_motion_export as hair


CandidateError = base.CandidateError
_need = base._need
HAIR_SCHEMA = 'cdesigner.hair-structure/1'
REVIEW_SCHEMA = 'cdesigner.reviewed-rest/1'
FOREARM_ENABLE_SCHEMA = 'cdesigner.reviewed-forearm-enable/1'
PROOF_SCHEMA = 'randomrealm.hair-structure-native/1'
PROOF_FLAGS = ('hairStructureApplyVerified', 'fullFileRollbackVerified', 'loadedStateRollbackVerified',
               'physicsProfileVerified', 'acceptedPartsVerified', 'nativeInitializationRebuilt',
               'nativePlayVerified', 'nonHairPreserved', 'scenesPreserved', 'selectionPreserved')
PROOF_SOURCE_PATHS = frozenset((
    'Assets/Scripts/Editor/CharacterTuningPartialCandidate.cs',
    'Assets/Scripts/Editor/CharacterTuningPartialAutoReceive.cs',
    'Assets/Scripts/Editor/CharacterTuningModelUpdate.cs',
    'Assets/Scripts/Editor/CharacterTuningFullReconciliation.cs',
    'Assets/Scripts/Editor/CharacterTuningAnimationLink.cs',
    'Assets/Scripts/Editor/CharacterTuningRetainedModelAliases.cs',
    'Assets/Scripts/Editor/AdventureCharacterAvatarRefresh.cs',
    'Assets/Scripts/Editor/AdventureCharacterSetupProfile.cs',
    'Assets/Scripts/Editor/AdventureCharacterSetupProfiles.cs',
    'Assets/Scripts/Editor/AdventureCharacterSetupRig.cs',
    'Assets/Scripts/Editor/CharacterHairMotionConversion.cs',
    'Assets/Scripts/Editor/CharacterHairMotionPreviewGuard.cs',
    'Assets/Scripts/Editor/CharacterHairStructureTransaction.cs',
    'Assets/Scripts/Editor/CharacterHairStructureSkin.cs',
    'Assets/Scripts/Editor/CharacterHairStructureNative.cs',
    'Assets/Scripts/Editor/CharacterHairStructureVerification.cs',
    'Assets/Scripts/Editor/CharacterHairStructureTransactionVerification.cs',
    'Assets/Scripts/Editor/CharacterReviewedLegRest.cs',
    'Assets/Scripts/Editor/CharacterReviewedForearmEnable.cs',
    'Assets/Scripts/Editor/CharacterCurrentRigAdmission.cs',
    'Assets/Scripts/Editor/CharacterHairStructureRawPrefab.cs',
    'Assets/Scripts/Editor/CharacterCurrentRigMenus.cs',
    'Assets/Scripts/Editor/CharacterCurrentRigProofContract.cs',
    'Assets/Scripts/Editor/CharacterHairStructureFixtureInstaller.cs',
    'Assets/Scripts/Editor/CharacterHairStructureSolverFixture.cs',
    'Assets/Scripts/Editor/CharacterPhysicsPersistence.cs',
    'Assets/Scripts/Editor/CharacterPhysicsProfile.cs',
    'Assets/Scripts/Editor/CharacterTuningPartialReceiver.cs',
    'Assets/Scripts/Editor/CharacterTuningPartialForearm.cs',
    'Assets/CharacterDesigner/Editor/HairMotionContractReader.cs',
    'Assets/CharacterDesigner/Runtime/HairMotionContract.cs',
    'Assets/CharacterDesigner/Editor/ForearmCorrectionImporter.cs',
    'Assets/CharacterDesigner/Runtime/ForearmCorrection.cs',
    'Assets/CharacterDesigner/Runtime/ForearmCorrectionData.cs',
    'Assets/CharacterDesigner/Runtime/ForearmCorrectionMath.cs',
    'Assets/Scripts/Gameplay/Character/Physics/CharacterHairMotionRig.cs',
    'Assets/Scripts/Gameplay/Character/Physics/CharacterPhysicsRig.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Editor/Cloth/ClothEditorManager.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Core/Cloth/ClothSerializeData2.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Core/Cloth/ClothSerializeData.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Core/Cloth/ClothSerializeDataFunction.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Core/Cloth/ClothProcess.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Core/Cloth/MagicaCloth.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Core/Cloth/SelectionData.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Core/VirtualMesh/VertexAttribute.cs',
    'Assets/Plugins/MagicaCloth2/Scripts/Core/Cloth/ClothInitSerializeData.cs'))
PROOF_SOURCE_NAMES = frozenset(path.rsplit('/', 1)[-1] for path in PROOF_SOURCE_PATHS)
SOURCE_INVENTORY_SCHEMA = 'randomrealm.current-rig-sources/1'
SOURCE_INVENTORY_SHA256 = hashlib.sha256(('\n'.join(sorted(PROOF_SOURCE_PATHS)) + '\n').encode('utf8')).hexdigest()
REVIEWED_NAMES = frozenset(('thigh.L', 'thigh.R', 'shin.L', 'shin.R'))
WORLD_SUMMARY_BUDGETS = {'head_meters': .00002, 'length_meters': .00002, 'axes_component': .00002}
REST_PROOF_FLAGS = ('reviewedRestPermissionVerified', 'reviewedRestApplyVerified',
                    'reviewedRestRollbackVerified', 'reviewedRestSkinBindingsVerified')
FOREARM_ENABLE_PROOF_FLAGS = ('reviewedForearmEnablePermissionVerified', 'reviewedForearmEnableApplyVerified',
    'reviewedForearmEnableRollbackVerified', 'reviewedForearmEnableCalibrationPreserved')
FOREARM_ENABLE_FIELDS = ('reviewed_forearm_enable_file', 'reviewed_forearm_enable_manifest_sha256',
    'reviewed_forearm_enable_receiver_revision', 'reviewed_forearm_enable_part_id',
    'reviewed_forearm_enable_sides', 'reviewed_forearm_enable_policy', 'reviewed_forearm_enable_source_snapshot_sha256')
AUTHORITY_FIELDS = ('project_root', 'asset_id', 'rig_id', 'target_guid', 'model_asset',
    'model_sha256', 'manifest_sha256', 'receiver_revision', 'source_uid',
    'candidate_fbx_sha256', 'candidate_manifest_sha256', 'ledger_sha256',
    'target_player_asset', 'target_player_sha256', 'physics_profile_asset', 'physics_profile_sha256', 'physics_profile_guid',
    'reviewed_bundle_manifest', 'source_snapshot_file', 'source_snapshot_sha256')


@dataclass(frozen=True)
class RigUpdatePlan:
    base: base.BaseSnapshot
    hair_source_name: str
    hair_source_uid: str
    hair_part_id: str
    requested_mesh_ids: tuple
    dependency_mesh_ids: tuple
    target_guid: str
    authority_json: str
    protected_pins: tuple
    review_pin: base.FilePin | None
    review_json: str | None
    candidate_fbx_sha256: str
    candidate_manifest_sha256: str
    bundle_manifest: Path
    source_snapshot_pin: base.FilePin
    source_snapshot_json: str
    forearm_enable_review_pin: base.FilePin | None = None
    forearm_enable_review_json: str | None = None


def _literal(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _read_hair(path, model, asset_id):
    record, pin = base._read_json(path)
    hair.validate_payload(record)
    _need(record['asset_id'] == asset_id and record['fbx_sha256'] == base._pin(model).sha256,
          'The Hair sidecar belongs to another character or FBX.')
    _need(len(record['strands']) == 32, 'Declare the complete32 stable Hair strands.')
    return record, pin


def _protected_inputs(snapshot):
    _need(snapshot.receiver_state_pin.sha256 is not None and bool(snapshot.receiver_revision),
          'An accepted partial ledger is required before a Rig update.')
    state, pin = base._read_json(snapshot.receiver_state_pin.path)
    _need(pin == snapshot.receiver_state_pin, 'The accepted ledger changed.')
    target = base._identity(state.get('target_guid'), 'accepted target GUID')
    saved_parts = state.get('parts')
    _need(type(saved_parts) is list and 0 < len(saved_parts) <= base.MAX_PARTS
          and all(type(part) is dict for part in saved_parts), 'The accepted part ledger is incomplete.')
    pins, publications = [pin], {}
    for part in saved_parts:
        for asset_key, digest_key in (('mesh_asset', 'mesh_sha256'), ('forearm_asset', 'forearm_sha256')):
            relative = part.get(asset_key)
            if not relative:
                continue
            _need(type(relative) is str and relative.startswith('Assets/'),
                  'An accepted generated asset is outside Unity Assets.')
            path = base._owned(snapshot.unity_project, relative)
            asset_pin = base._pin(path)
            _need(asset_pin.sha256 == base._digest(part.get(digest_key), digest_key),
                  'An accepted generated asset changed: ' + relative)
            pins.extend((asset_pin, base._pin(Path(str(path) + '.meta'))))
            if asset_key == 'mesh_asset':
                revision = base._identity(path.stem, 'Accepted mesh revision')
                publications.setdefault(revision, set()).add(part['part_id'])
    for revision, selected in publications.items():
        marker = snapshot.unity_project / '.codex/character-update-inbox' / snapshot.asset_id / revision / 'candidate.json'
        record, marker_pin = base._read_json(marker)
        _need(record.get('schema') == base.SCHEMA and record.get('ok') is True
              and record.get('asset_id') == snapshot.asset_id and record.get('rig_id') == snapshot.rig_id
              and record.get('update_id') == revision
              and type(record.get('selection')) is dict
              and type(record['selection'].get('mesh_ids')) is list
              and selected <= set(record['selection']['mesh_ids']),
              'An accepted repair publication changed identity or ownership.')
        files = record.get('files')
        _need(type(files) is dict and 0 < len(files) <= base.MAX_FILES,
              'An accepted repair publication lacks its bounded payload inventory.')
        pins.append(marker_pin)
        for relative, digest in files.items():
            payload = base._pin(base._owned(marker.parent, relative, payload=True))
            _need(payload.sha256 == base._digest(digest, relative), 'An accepted repair input changed.')
            pins.append(payload)
    return target, tuple(pins)


def _review_packet(snapshot, review_file):
    if review_file is None:
        return None, None, ()
    path = base._guard_path(base._absolute(review_file, 'Reviewed Rest packet'))
    review, pin = base._read_json(path)
    names = review.get('rows')
    _need(review.get('schema') == REVIEW_SCHEMA and review.get('world_summary_budgets') == WORLD_SUMMARY_BUDGETS
          and type(names) is list and len(names) == 4
          and all(type(row) is dict for row in names)
          and {row.get('name') for row in names} == REVIEWED_NAMES,
          'Use the exact four-row reviewed current-Rig permission packet.')
    _need(path.name == snapshot.model_file.with_suffix('.reviewed-rest.json').name
          and review.get('source_asset_id') == snapshot.asset_id
          and review.get('receiver_revision') == snapshot.receiver_revision
          and review.get('baseline_fbx_sha256') == snapshot.model_sha256
          and review.get('baseline_manifest_sha256') == snapshot.manifest_sha256,
          'The reviewed Rest packet has another accepted character or revision.')
    for field in ('candidate_fbx_sha256', 'candidate_manifest_sha256', 'reviewed_diff_sha256'):
        base._digest(review.get(field), field)
    _need(type(review.get('world_summary_residuals')) is list, 'Disclose every nonzero world-summary residual.')
    evidence = []
    for label in ('baseline', 'candidate'):
        path = base._guard_path(base._absolute(review.get(label + '_manifest_file'), label + ' sealed manifest'))
        _need(not base._within(path, snapshot.unity_project)
              and not base._same(path, snapshot.manifest_file), 'Keep sealed manifest evidence outside Unity.')
        item = base._pin(path)
        _need(item.sha256 == review[label + '_manifest_sha256'], 'The sealed manifest evidence changed.')
        evidence.append(item)
    for row in names:
        _bone_record(row.get('before')); _bone_record(row.get('after'))
        _need(row['before']['name'] == row['name'] == row['after']['name'], 'A reviewed bone name was reassigned.')
    return review, pin, tuple(evidence)


def _announced_review(snapshot, explicit):
    record, _ = base._read_json(snapshot.unity_project / '.codex/character-update-receiver.json')
    if record.get('supports_current_rig_reviewed_rest') is not True:
        return explicit
    report = base._guard_path(base._absolute(record.get('hair_structure_verification_report'), 'Native report'))
    _need(base._within(report, snapshot.unity_project / '.codex'), 'The native report belongs to another project.')
    proof, _ = base._read_json(report)
    value = record.get('reviewed_rest_file')
    _need(type(value) is str and value == proof.get('reviewed_rest_file'),
          'The dedicated receiver does not identify its exact reviewed Rest file.')
    path = base._guard_path(base._absolute(value, 'Native reviewed Rest file'))
    if explicit is not None:
        _need(base._same(path, base._absolute(explicit, 'Selected reviewed Rest file')),
              'The selected Rest packet differs from the receiver native proof.')
    return path


def _forearm_enable_review(snapshot, record, proof, target_guid, rest_review, explicit=None):
    """Prove only two reviewed Off flags; retain every other calibration token."""
    enabled = record.get('supports_current_rig_reviewed_forearm_enable', False)
    _need(type(enabled) is bool, 'The reviewed Forearm enable capability must be Boolean.')
    if not enabled:
        _need(explicit is None, 'Reviewed Forearm enable reception is not enabled.')
        return None, None, ()
    _need(rest_review is not None, 'Forearm enable permission requires the complete Hair + reviewed Rest scope.')
    for flag in FOREARM_ENABLE_PROOF_FLAGS:
        _need(proof.get(flag) is True, 'The Forearm enable native proof is incomplete: ' + flag)
    for key in FOREARM_ENABLE_FIELDS:
        _need(key in proof and record.get(key) == proof[key], 'The native Forearm enable announcement/report disagree: ' + key)
    path = base._guard_path(base._absolute(proof['reviewed_forearm_enable_file'], 'Reviewed Forearm enable file'))
    _need(not base._within(path, snapshot.unity_project), 'Keep the immutable reviewed Forearm file outside Unity.')
    _need(path.name == snapshot.model_file.with_suffix('.reviewed-forearm-enable.json').name,
          'The Forearm permission file names another model.')
    if explicit is not None:
        _need(base._same(path, base._absolute(explicit, 'Selected Forearm enable review')),
              'The selected Forearm permission differs from the native proof.')
    review, pin = base._read_json(path)
    _need(pin.sha256 == base._digest(proof['reviewed_forearm_enable_manifest_sha256'], 'Forearm enable review SHA256')
          and review.get('schema') == FOREARM_ENABLE_SCHEMA and review.get('policy') == 'use_current_blender',
          'The exact reviewed current-Blender Forearm permission changed.')
    expected = {'source_asset_id': snapshot.asset_id, 'rig_id': snapshot.rig_id, 'target_guid': target_guid,
        'receiver_revision': snapshot.receiver_revision, 'ledger_sha256': snapshot.receiver_state_pin.sha256,
        'baseline_fbx_sha256': snapshot.model_sha256, 'baseline_manifest_sha256': snapshot.manifest_sha256,
        'candidate_fbx_sha256': proof['candidate_fbx_sha256'], 'candidate_manifest_sha256': proof['candidate_manifest_sha256'],
        'source_snapshot_sha256': proof['source_snapshot_sha256']}
    _need(all(review.get(key) == value for key, value in expected.items()),
          'The Forearm enable permission belongs to another source, target, baseline or candidate.')
    ledger = base._guard_path(base._absolute(review.get('ledger_file'), 'Reviewed original ledger'))
    _need(base._same(ledger, snapshot.receiver_state_pin.path), 'The Forearm permission names another original ledger.')
    rows = review.get('rows')
    fields = {'part_id', 'renderer_path', 'object_name', 'side', 'before', 'after'}
    _need(type(rows) is list and len(rows) == 2 and all(type(row) is dict and set(row) == fields for row in rows)
          and [row['side'] for row in rows] == ['L', 'R']
          and all(row['before'] is True and row['after'] is False for row in rows),
          'Authorize exactly two literal L/R enabled True to False changes.')
    part_id = base._identity(rows[0]['part_id'], 'Reviewed Forearm part ID')
    object_name = base._text(rows[0]['object_name'], 'Reviewed Forearm object')
    renderer = base._text(rows[0]['renderer_path'], 'Reviewed Forearm renderer')
    _need(all(row['part_id'] == part_id and row['object_name'] == object_name and row['renderer_path'] == renderer for row in rows),
          'Both Forearm changes must belong to the same existing Body part.')
    state, ledger_pin = base._read_json(ledger)
    accepted = [row for row in state.get('parts', []) if row.get('part_id') == part_id]
    formal, _ = base._read_json(snapshot.manifest_file)
    _need(ledger_pin == snapshot.receiver_state_pin and len(accepted) == 1
          and accepted[0].get('renderer_path') == renderer and object_name in formal.get('meshes', {}),
          'The reviewed Forearm part differs from the accepted registered renderer.')
    _need(proof['reviewed_forearm_enable_receiver_revision'] == snapshot.receiver_revision
          and proof['reviewed_forearm_enable_part_id'] == part_id
          and proof['reviewed_forearm_enable_sides'] == ['L', 'R']
          and proof['reviewed_forearm_enable_policy'] == 'use_current_blender'
          and proof['reviewed_forearm_enable_source_snapshot_sha256'] == proof['source_snapshot_sha256'],
          'The native Forearm enable proof names another permission/source snapshot.')
    evidence = [pin, ledger_pin]
    candidate_manifest_record = None
    for label in ('baseline', 'candidate'):
        manifest = base._guard_path(base._absolute(review.get(label + '_manifest_file'), label + ' Forearm sealed manifest'))
        _need(not base._within(manifest, snapshot.unity_project), 'Keep sealed Forearm manifest evidence outside Unity.')
        manifest_record, manifest_pin = base._read_json(manifest)
        _need(manifest_pin.sha256 == review[label + '_manifest_sha256']
              and manifest_record.get('asset_id') == snapshot.asset_id
              and manifest_record.get('filename') == snapshot.model_file.name,
              'A Forearm permission manifest pin changed.')
        if label == 'candidate':
            candidate_manifest_record = manifest_record
            _need(manifest_record.get('files', {}).get(snapshot.model_file.with_suffix('.forearm.json').name)
                  == review.get('candidate_forearm_sha256'), 'The reviewed new Forearm file is not owned by the candidate manifest.')
        evidence.append(manifest_pin)
    reference_manifest = base._guard_path(base._absolute(review.get('reference_manifest_file'), 'Reference current-Rig manifest'))
    _need(not base._within(reference_manifest, snapshot.unity_project), 'Keep the original current-Rig manifest outside Unity.')
    reference_record, reference_pin = base._read_json(reference_manifest)
    forearm_name = snapshot.model_file.with_suffix('.forearm.json').name
    _need(reference_pin.sha256 == base._digest(review.get('reference_manifest_sha256'), 'Reference current-Rig manifest SHA256')
          and reference_record.get('asset_id') == snapshot.asset_id
          and reference_record.get('filename') == snapshot.model_file.name
          and reference_record.get('files', {}).get(snapshot.model_file.name) == proof['candidate_fbx_sha256']
          and reference_record.get('files', {}).get(forearm_name) == review.get('reference_forearm_sha256'),
          'The sealed current-Rig reference manifest changed identity or original Forearm ownership.')
    expected_manifest = json.loads(_literal(reference_record))
    expected_manifest['files'][forearm_name] = review['candidate_forearm_sha256']
    _need(_literal(expected_manifest) == _literal(candidate_manifest_record),
          'The derived complete manifest changes beyond its reviewed Forearm checksum.')
    evidence.append(reference_pin)
    source_record, _ = base._read_json(base._absolute(proof['source_snapshot_file'], 'Reviewed current source snapshot'))
    source_forearm = source_record['definition']['objects'].get(object_name, {}).get('mesh', {}).get('forearm_definition')
    _need(type(source_forearm) is dict and type(source_forearm.get('sides')) is list
          and type(source_forearm.get('authored_calibration_records')) is dict,
          'The reviewed source snapshot lacks the complete current Forearm definition.')
    for side in ('L', 'R'):
        current = [row for row in source_forearm['sides'] if type(row) is dict and row.get('name') == side]
        _need(len(current) == 1 and current[0].get('enabled') is False
              and source_forearm['authored_calibration_records'].get(side, {}).get('enabled') is False,
              'The reviewed source snapshot does not prove the exact current disabled Forearm flags.')
    payloads = []
    for label in ('reference', 'candidate'):
        item = base._guard_path(base._absolute(review.get(label + '_forearm_file'), label + ' Forearm companion'))
        _need(not base._within(item, snapshot.unity_project), 'Keep reviewed Forearm companions outside Unity.')
        payload, payload_pin = base._read_json(item)
        _need(payload_pin.sha256 == base._digest(review.get(label + '_forearm_sha256'), label + ' Forearm SHA256')
              and payload.get('schema') == 'cdesigner.forearm/1' and payload.get('fbx') == snapshot.model_file.name
              and payload.get('fbxSha256') == proof['candidate_fbx_sha256'],
              'A reviewed Forearm companion changed identity or FBX.')
        payloads.append(payload); evidence.append(payload_pin)
    reference, candidate = payloads
    expected_payload = json.loads(_literal(reference))
    meshes = expected_payload.get('meshes')
    _need(type(meshes) is list and all(type(mesh) is dict for mesh in meshes), 'Missing reviewed Forearm mesh inventory.')
    body = [mesh for mesh in meshes if mesh.get('objectName') == object_name]
    _need(len(body) == 1 and type(body[0].get('sides')) is list, 'The Forearm reference lacks the exact reviewed Body.')
    for row in rows:
        sides = [side for side in body[0]['sides'] if type(side) is dict and side.get('name') == row['side']]
        _need(len(sides) == 1 and sides[0].get('enabled') is True, 'A reviewed original Forearm flag is not literal True.')
        sides[0]['enabled'] = False
    _need(_literal(expected_payload) == _literal(candidate),
          'The new Forearm companion changes calibration beyond the two reviewed disabled flags.')
    return review, pin, tuple(evidence)


def _capability(snapshot, target_guid, source_uid, review, review_pin, bundle_manifest=None, forearm_enable_review_file=None):
    generic = base.receiver_capabilities(snapshot.model_file, unity_project=snapshot.unity_project)
    _need(generic is not None and generic['supports_rig'] is False and generic['supports_full_reconcile'] is False,
          'This route needs a dedicated Hair receiver; generic Rig/full remains closed.')
    announcement = snapshot.unity_project / '.codex/character-update-receiver.json'
    record, announcement_pin = base._read_json(announcement)
    _need(record.get('supports_hair_structure') is True, 'Dedicated native-verified Hair structure reception is not enabled.')
    report_path = base._guard_path(base._absolute(record.get('hair_structure_verification_report'), 'Native Hair report'))
    _need(base._within(report_path, snapshot.unity_project / '.codex'), 'The native report belongs to another project.')
    proof, proof_pin = base._read_json(report_path)
    _need(proof_pin.sha256 == base._digest(record.get('hair_structure_verification_report_sha256'), 'Native report SHA256')
          and proof.get('schema') == PROOF_SCHEMA and proof.get('passed') is True,
          'The dedicated native Hair report changed or failed.')
    for key in AUTHORITY_FIELDS:
        _need(record.get(key) == proof.get(key) and type(proof.get(key)) is str,
              'The native announcement and report disagree on ' + key + '.')
    for flag in PROOF_FLAGS:
        _need(proof.get(flag) is True, 'The native Hair proof is incomplete: ' + flag)
    sources = record.get('hair_structure_verified_sources')
    _need(type(sources) is dict and len(PROOF_SOURCE_PATHS) <= len(sources) <= 64
          and proof.get('hair_structure_verified_sources') == sources
          and PROOF_SOURCE_PATHS <= set(sources)
          and record.get('hair_structure_source_inventory_schema') == proof.get('hair_structure_source_inventory_schema')
              == SOURCE_INVENTORY_SCHEMA
          and record.get('hair_structure_source_inventory_sha256') == proof.get('hair_structure_source_inventory_sha256')
              == SOURCE_INVENTORY_SHA256,
          'The native report lacks its exact receiver source inventory.')
    pins = [announcement_pin, proof_pin]
    for relative, digest in sources.items():
        relative = base._relative(relative, 'Native verified source')
        _need(relative.startswith('Assets/') and relative.endswith('.cs'), 'A verified source is outside Unity Assets.')
        source_pin = base._pin(base._owned(snapshot.unity_project, relative))
        _need(source_pin.sha256 == base._digest(digest, relative), 'A native-verified receiver source changed: ' + relative)
        pins.append(source_pin)
    version_path = snapshot.unity_project / 'ProjectSettings/ProjectVersion.txt'
    version_pin = base._pin(version_path)
    _need(version_pin.size < 4096, 'Invalid Unity version record.')
    versions = re.findall(r'^m_EditorVersion:\s*(\S+)\s*$', base._io(version_path).read_text(encoding='utf-8-sig'), re.M)
    _need(len(versions) == 1 and proof.get('unityVersion') == versions[0], 'The native report names another Unity version.')
    pins.append(version_pin)
    pins.extend(_verify_report_binding(proof, snapshot, target_guid, source_uid, bundle_manifest))
    if review is not None:
        _need(record.get('supports_current_rig_reviewed_rest') is True,
              'Exact reviewed current-Rig Rest reception is not enabled.')
        for flag in REST_PROOF_FLAGS:
            _need(proof.get(flag) is True, 'The reviewed Rest native proof is incomplete: ' + flag)
        _need(proof.get('reviewed_rest_manifest_sha256') == review_pin.sha256
              and proof.get('reviewed_rest_receiver_revision') == snapshot.receiver_revision
              and proof.get('reviewed_rest_names') == sorted(REVIEWED_NAMES)
              and proof.get('reviewed_diff_sha256') == review['reviewed_diff_sha256'],
              'The native Rest proof belongs to another permission packet.')
    _, _, enable_pins = _forearm_enable_review(snapshot, record, proof, target_guid, review, forearm_enable_review_file)
    pins.extend(enable_pins)
    base._text(record.get('hair_structure_native_verified_utc'), 'Native verification UTC', 128)
    for pin in pins:
        base._verify(pin)
    return _literal({'announcement': record, 'proof': proof}), tuple(pins), proof


def _verify_report_binding(proof, snapshot, target_guid, source_uid, bundle_manifest):
    """Exact production binding; field names follow the receiver contract."""
    expected = {'target_guid': target_guid, 'asset_id': snapshot.asset_id, 'rig_id': snapshot.rig_id,
        'project_root': str(snapshot.unity_project), 'model_sha256': snapshot.model_sha256,
        'model_asset': snapshot.model_file.relative_to(snapshot.unity_project).as_posix(),
        'manifest_sha256': snapshot.manifest_sha256, 'receiver_revision': snapshot.receiver_revision,
        'source_uid': source_uid, 'ledger_sha256': snapshot.receiver_state_pin.sha256}
    _need(all(proof.get(key) == value for key, value in expected.items()),
          'The native proof belongs to another project, target, Hair source or accepted revision.')
    for key in ('candidate_fbx_sha256', 'candidate_manifest_sha256'):
        base._digest(proof.get(key), key)
    pins = []
    for path_field, digest_field in (('target_player_asset', 'target_player_sha256'),
                                     ('physics_profile_asset', 'physics_profile_sha256')):
        relative = base._relative(proof.get(path_field), path_field)
        _need(relative.startswith('Assets/'), 'The native target/profile is outside Unity Assets.')
        path = base._owned(snapshot.unity_project, relative)
        pin, meta = base._pin(path), base._pin(Path(str(path) + '.meta'))
        _need(pin.sha256 == base._digest(proof.get(digest_field), digest_field),
              'The native-verified target Player or physics profile changed.')
        pins.extend((pin, meta))
        _need(meta.size < 65536, 'Invalid native target/profile metadata.')
        ids = re.findall(r'^guid:\s*([0-9a-fA-F]{32})\s*$', base._io(meta.path).read_text(encoding='utf-8-sig'), re.M)
        expected_guid = (target_guid if path_field == 'target_player_asset' else
                         base._identity(proof.get('physics_profile_guid'), 'Native physics profile GUID'))
        _need(len(ids) == 1 and ids[0].lower() == expected_guid, 'The native target Player or physics profile GUID changed.')
    path = base._guard_path(base._absolute(proof.get('reviewed_bundle_manifest'), 'Native reviewed complete manifest'))
    _need(not base._within(path, snapshot.unity_project), 'Keep the reviewed export bundle outside Unity.')
    if bundle_manifest is not None:
        _need(base._same(path, base._absolute(bundle_manifest, 'Selected complete manifest')),
              'The selected bundle path differs from the exact native-verified candidate.')
    record, pin = base._read_json(path)
    model = base._pin(base._owned(path.parent, snapshot.model_file.name, payload=True))
    _need(pin.sha256 == proof['candidate_manifest_sha256'] and model.sha256 == proof['candidate_fbx_sha256']
          and record.get('asset_id') == snapshot.asset_id and record.get('filename') == snapshot.model_file.name,
          'The selected bundle differs from the exact native-verified candidate.')
    pins.extend((pin, model))
    source_path = base._guard_path(base._absolute(proof.get('source_snapshot_file'), 'Native Rig source snapshot'))
    source_record, source_pin = base._read_json(source_path)
    definition = source_record.get('definition')
    _need(source_pin.sha256 == base._digest(proof.get('source_snapshot_sha256'), 'Native source snapshot SHA256')
          and source_record.get('schema') == 'cdesigner.rig-source/1'
          and source_record.get('asset_id') == snapshot.asset_id and source_record.get('rig_id') == snapshot.rig_id
          and source_record.get('candidate_fbx_sha256') == proof['candidate_fbx_sha256']
          and source_record.get('candidate_manifest_sha256') == proof['candidate_manifest_sha256']
          and type(definition) is dict and definition.get('schema') == 'cdesigner.rig-source-definition/2'
          and definition.get('rig_id') == snapshot.rig_id and definition.get('main_rig') == record.get('source_rig')
          and type(definition.get('unit_scale')) in (float, int) and math.isfinite(definition['unit_scale'])
          and definition['unit_scale'] > 0 and type(definition.get('objects')) is dict
          and type(record.get('objects')) is list and set(definition['objects']) == set(record['objects'])
          and all(type(item) is dict for item in definition['objects'].values())
          and type(definition.get('dependencies')) is dict
          and hashlib.sha256(_literal(definition).encode('utf8')).hexdigest()
              == base._digest(source_record.get('definition_sha256'), 'Native source definition SHA256'),
          'The native Rig source snapshot is incomplete, changed or belongs to another candidate.')
    pins.append(source_pin)
    return tuple(pins)


def capture_plan(base_snapshot, *, hair_source_name, hair_source_uid, requested_mesh_ids,
                 review_file=None, bundle_manifest=None, source_snapshot=None, forearm_enable_review_file=None):
    """Capture fresh explicit intent and native receiver authority before export."""
    base.revalidate_base(base_snapshot)
    name = base._text(hair_source_name, 'Native Hair source name')
    uid = base._identity(hair_source_uid, 'Native Hair source UID')
    matches = [part for part in base_snapshot.parts if part.object_name == name]
    _need(len(matches) == 1 and type(requested_mesh_ids) in (list, tuple)
          and list(requested_mesh_ids) == [matches[0].part_id], 'Select exactly Rig + the proven Hair Mesh.')
    target, protected = _protected_inputs(base_snapshot)
    formal, _ = base._read_json(base_snapshot.manifest_file)
    _need(formal.get('hair_motion', {}).get('active') is True, 'Retain the accepted active Hair companion.')
    old, old_pin = _read_hair(base_snapshot.model_file.with_suffix('.hair-motion.json'), base_snapshot.model_file, base_snapshot.asset_id)
    _need(old['source_name'] == name and old['source_uid'] == uid, 'The native Hair source differs from the accepted stable source.')
    review, review_pin, evidence = _review_packet(base_snapshot, _announced_review(base_snapshot, review_file))
    authority, authority_pins, proof = _capability(base_snapshot, target, uid, review, review_pin, bundle_manifest,
        forearm_enable_review_file)
    source_record, source_pin = base._read_json(base._absolute(proof['source_snapshot_file'], 'Native source snapshot'))
    enable_review, enable_pin = (base._read_json(base._absolute(proof['reviewed_forearm_enable_file'], 'Native Forearm review'))
        if json.loads(authority)['announcement'].get('supports_current_rig_reviewed_forearm_enable') is True else (None, None))
    plan = RigUpdatePlan(base_snapshot, name, uid, matches[0].part_id, tuple(requested_mesh_ids),
        tuple(part.part_id for part in base_snapshot.parts if part.part_id != matches[0].part_id), target,
        authority, tuple([*protected, old_pin, *authority_pins, *evidence]), review_pin,
        _literal(review) if review is not None else None, proof['candidate_fbx_sha256'], proof['candidate_manifest_sha256'],
        base._absolute(proof['reviewed_bundle_manifest'], 'Native reviewed manifest'), source_pin, _literal(source_record),
        enable_pin, _literal(enable_review) if enable_review is not None else None)
    validate_source_snapshot(plan, source_snapshot)
    revalidate_plan(plan)
    return plan


def revalidate_plan(plan, *, source_snapshot=None):
    """Refuse any captured baseline, native authority or reviewed-permission drift."""
    _need(isinstance(plan, RigUpdatePlan), 'Capture a trusted Rig update plan first.')
    base.revalidate_base(plan.base)
    for pin in plan.protected_pins:
        base._verify(pin)
    review = None
    if plan.review_pin is not None:
        review, pin = base._read_json(plan.review_pin.path)
        _need(pin == plan.review_pin and _literal(review) == plan.review_json, 'The reviewed Rest permission changed.')
    if plan.forearm_enable_review_pin is not None:
        enable_review, enable_pin = base._read_json(plan.forearm_enable_review_pin.path)
        _need(enable_pin == plan.forearm_enable_review_pin and _literal(enable_review) == plan.forearm_enable_review_json,
              'The reviewed Forearm enable permission changed.')
    authority, _, _ = _capability(plan.base, plan.target_guid, plan.hair_source_uid, review, plan.review_pin)
    _need(authority == plan.authority_json, 'The dedicated Rig receiver authority changed during export.')
    if source_snapshot is not None:
        validate_source_snapshot(plan, source_snapshot)


def validate_source_snapshot(plan, source_snapshot):
    """Compare a fresh caller-supplied native definition without reading Blender."""
    _need(isinstance(plan, RigUpdatePlan) and type(source_snapshot) is dict,
          'Read the current native Rig source definition before sending its reviewed bundle.')
    expected = json.loads(plan.source_snapshot_json)
    _need(_literal(source_snapshot) == _literal(expected['definition'])
          and hashlib.sha256(_literal(source_snapshot).encode('utf8')).hexdigest() == expected['definition_sha256'],
          'The current native Rig source changed since this exact bundle was reviewed.')


def read_capabilities(model_file, review_file=None, bundle_manifest=None):
    """Fresh disk-only display metadata; actions still require capture_plan.

    No native part mapping is inferred here. The minimal disk context validates
    the report's accepted bundle, ledger and explicit target/profile identities.
    An absent/disabled dedicated receiver returns None; malformed proofs refuse.
    """
    model = base._guard_path(base._absolute(model_file, 'Accepted model'))
    project = base._project(model, None)
    announcement = project / '.codex/character-update-receiver.json'
    if not os.path.lexists(base._io(announcement)):
        return None
    record, _ = base._read_json(announcement)
    if record.get('supports_hair_structure') is False or 'supports_hair_structure' not in record:
        return None
    _need(record.get('supports_hair_structure') is True, 'The dedicated Hair capability must be an explicit Boolean.')
    formal, formal_pin = base._read_json(model.with_suffix('.cdesigner.json'))
    asset_id = base._identity(formal.get('asset_id'), 'accepted asset_id')
    state_path = project / '.codex/character-update-state' / (asset_id + '.json')
    state, state_pin = base._read_json(state_path)
    model_pin = base._pin(model)
    _need(formal.get('schema') == base.BASE_SCHEMA and formal.get('ok') is True
          and state.get('schema') == base.RECEIVER_SCHEMA and state.get('asset_id') == asset_id
          and state.get('model_sha256') == model_pin.sha256 and state.get('manifest_sha256') == formal_pin.sha256,
          'The receiver no longer describes this accepted model bundle.')
    pins = [formal_pin]
    files = formal.get('files')
    _need(type(files) is dict and model.name in files and 0 < len(files) <= base.MAX_FILES,
          'The accepted complete file inventory is missing.')
    for relative, digest in files.items():
        pin = base._pin(base._owned(model.parent, relative))
        _need(pin.sha256 == base._digest(digest, relative), 'An accepted model payload changed.')
        pins.append(pin)
    context = base.BaseSnapshot(project, model, formal_pin.path, asset_id,
        base._identity(state.get('rig_id'), 'accepted rig_id'), (), model_pin.sha256, formal_pin.sha256,
        base._identity(state.get('receiver_revision'), 'accepted receiver_revision'), tuple(pins), state_pin)
    target, protected = _protected_inputs(context)
    old, old_pin = _read_hair(model.with_suffix('.hair-motion.json'), model, asset_id)
    review, review_pin, evidence = _review_packet(context, _announced_review(context, review_file))
    authority, authority_pins, proof = _capability(context, target, old['source_uid'], review, review_pin, bundle_manifest)
    for pin in (*protected, old_pin, *evidence, *authority_pins):
        base._verify(pin)
    base.revalidate_base(context)
    return {'supports_hair_structure': True, 'supports_rig': False, 'supports_full_reconcile': False,
        'supports_current_rig_reviewed_rest': review is not None,
        'supports_current_rig_reviewed_forearm_enable': json.loads(authority)['announcement'].get('supports_current_rig_reviewed_forearm_enable') is True,
        'target_guid': target, 'source_uid': old['source_uid'], 'unityVersion': proof['unityVersion'],
        'candidate_fbx_sha256': proof['candidate_fbx_sha256'],
        'candidate_manifest_sha256': proof['candidate_manifest_sha256'],
        'bundle_manifest': proof['reviewed_bundle_manifest'], 'source_snapshot_file': proof['source_snapshot_file'],
        'authority_sha256': hashlib.sha256(authority.encode('utf8')).hexdigest()}


def _bone_record(value):
    fields = {'name', 'parent', 'head', 'axes', 'length', 'terminal', 'restore'}
    _need(type(value) is dict and set(value) == fields and type(value['name']) is str and bool(value['name'])
          and (value['parent'] is None or type(value['parent']) is str)
          and type(value['terminal']) is bool and type(value['restore']) is bool,
          'An explicit Rest row is incomplete or has ambiguous fields.')
    def numbers(items, count):
        return type(items) is list and len(items) == count and all(type(item) in (int, float) and math.isfinite(item) for item in items)
    _need(numbers(value['head'], 3) and type(value['axes']) is list and len(value['axes']) == 3
          and all(numbers(axis, 3) for axis in value['axes'])
          and type(value['length']) in (int, float) and math.isfinite(value['length']) and value['length'] > 0,
          'An explicit Rest row has invalid units, dimensions or nonfinite values.')


def _rest_index(manifest, model_hash):
    rest = manifest.get('bone_rest_lengths')
    _need(type(rest) is dict and rest.get('schema') == 'cdesigner.bone-rest-lengths/1'
          and rest.get('space') == 'export-world-meters' and rest.get('fbx_sha256') == model_hash
          and rest.get('rig_name') == manifest.get('source_rig')
          and type(rest.get('bones')) is list and rest['bones'], 'Use the complete FBX-bound meter-space Rest summary.')
    result = {}
    for bone in rest['bones']:
        _bone_record(bone)
        _need(bone['name'] not in result, 'The Rest summary repeats a bone name.')
        result[bone['name']] = bone
    for bone in result.values():
        parent = bone['parent']; seen = {bone['name']}
        while parent is not None:
            _need(parent in result and parent not in seen, 'The Rest summary has a missing parent or cycle.')
            seen.add(parent); parent = result[parent]['parent']
    return result


def _near(first, second, tolerance=1e-6):
    if type(first) in (int, float) and type(second) in (int, float):
        return math.isfinite(first) and math.isfinite(second) and abs(first - second) <= tolerance
    if type(first) is list and type(second) is list:
        return len(first) == len(second) and all(_near(a, b, tolerance) for a, b in zip(first, second))
    if type(first) is dict and type(second) is dict:
        return first.keys() == second.keys() and all(_near(first[key], second[key], tolerance) for key in first)
    return type(first) is type(second) and first == second


def _world_delta(before, after):
    return {'head_max_delta_meters': max(abs(a - b) for a, b in zip(before['head'], after['head'])),
        'axes_max_component_delta_dimensionless': max(abs(a - b) for x, y in zip(before['axes'], after['axes']) for a, b in zip(x, y)),
        'length_delta_meters': abs(before['length'] - after['length'])}


def _world_within(before, after):
    if any(before[key] != after[key] for key in before if key not in {'head', 'axes', 'length'}):
        return False
    delta = _world_delta(before, after)
    return (delta['head_max_delta_meters'] <= WORLD_SUMMARY_BUDGETS['head_meters']
        and delta['length_delta_meters'] <= WORLD_SUMMARY_BUDGETS['length_meters']
        and delta['axes_max_component_delta_dimensionless'] <= WORLD_SUMMARY_BUDGETS['axes_component'])


def _structure(plan, old, new, formal, complete, model_hash, manifest_hash):
    _need(old['source_uid'] == new['source_uid'] == plan.hair_source_uid
          and old['source_name'] == new['source_name'] == plan.hair_source_name and old['registry'] == new['registry'],
          'The Hair source identity or source topology changed.')
    before = {strand['strand_id']: strand for strand in old['strands']}
    after = {strand['strand_id']: strand for strand in new['strands']}
    _need(before.keys() == after.keys() and len(before) == 32, 'The complete stable32 Hair inventory changed.')
    changed = False
    for identity, previous in before.items():
        strand = after[identity]
        _need(all(previous.get(key) == strand.get(key) for key in ('chain_id', 'signature', 'order', 'vertices',
            'layers', 'pair_id', 'mirror_id', 'side', 'pair_proof', 'vertex_map', 'boundary_map')),
            'A stable Hair strand or mirror owner was reassigned.')
        changed |= len(previous['exported_bones']) != len(strand['exported_bones'])
    _need(changed, 'This bounded Rig route requires a Hair chain-count change.')
    old_rest = _rest_index(formal, plan.base.model_sha256)
    new_rest = _rest_index(complete, model_hash)
    old_hair = {name for strand in old['strands'] for name in strand['exported_bones']}
    new_hair = {name for strand in new['strands'] for name in strand['exported_bones']}
    _need(old_hair <= old_rest.keys() and new_hair <= new_rest.keys(), 'Hair bindings are missing from the Rest summaries.')
    protected = old_rest.keys() - old_hair
    _need(protected == new_rest.keys() - new_hair, 'The non-Hair skeleton inventory changed.')
    if plan.review_json is None:
        _need(all(_near(old_rest[name], new_rest[name]) for name in protected),
              'Non-Hair Rest changed outside the Hair-only update scope.')
        return
    review = json.loads(plan.review_json)
    _need(review['candidate_fbx_sha256'] == model_hash and review['candidate_manifest_sha256'] == manifest_hash,
          'The worker bundle differs from the exact reviewed current Rig.')
    _need(REVIEWED_NAMES <= protected, 'The four reviewed Rest rows are missing.')
    changed_names = {name for name in protected if not _world_within(old_rest[name], new_rest[name])}
    _need(changed_names == REVIEWED_NAMES, 'Rest outside the exact four reviewed rows exceeds the explicit combined budget.')
    rows = {row['name']: row for row in review['rows']}
    _need(all(_literal(rows[name]['before']) == _literal(old_rest[name])
              and _literal(rows[name]['after']) == _literal(new_rest[name]) for name in REVIEWED_NAMES),
          'A literal reviewed before/after Rest row changed.')
    residuals = [{'name': name, 'before': old_rest[name], 'after': new_rest[name], **_world_delta(old_rest[name], new_rest[name]),
        'classification': 'nonzero_raw_summary_drift_within_explicit_combined_budget_not_exactly_unchanged'}
        for name in sorted(protected - REVIEWED_NAMES) if _literal(old_rest[name]) != _literal(new_rest[name])]
    _need(_literal(residuals) == _literal(review['world_summary_residuals']), 'The raw world-summary residual disclosure changed.')


def _bundle(plan, stage, model_file, files, parts):
    _need(base._parts(parts) == plan.base.parts, 'The complete stable part inventory changed.')
    _need(model_file == plan.base.model_file.name, 'The Rig candidate names another model.')
    _need(type(files) in (list, tuple) and 0 < len(files) < base.MAX_FILES, 'Declare the complete worker file inventory.')
    pins, hashes = [], {}
    for relative in files:
        relative = base._relative(relative, payload=True)
        _need(relative.casefold() not in {name.casefold() for name in hashes}, 'The worker files contain aliases.')
        pin = base._pin(base._owned(stage, relative, payload=True))
        _need(pin.size > 0, 'The worker produced an empty payload.')
        pins.append((relative, pin)); hashes[relative] = pin.sha256
    manifest_name = Path(model_file).with_suffix('.cdesigner.json').name
    _need(manifest_name in hashes and model_file in hashes, 'A Rig candidate requires its actual complete manifest and FBX.')
    complete, complete_pin = base._read_json(stage / manifest_name)
    _need(hashes[model_file] == plan.candidate_fbx_sha256 and complete_pin.sha256 == plan.candidate_manifest_sha256,
          'The worker bundle differs from the exact native-verified Rig candidate.')
    formal, _ = base._read_json(plan.base.manifest_file)
    _need(complete.get('schema') == base.BASE_SCHEMA and complete.get('ok') is True
          and complete.get('asset_id') == plan.base.asset_id and complete.get('filename') == model_file
          and complete.get('source_rig') == formal.get('source_rig'), 'The complete worker bundle identity changed.')
    names = {part.object_name for part in plan.base.parts}
    _need(type(complete.get('meshes')) is dict and set(complete['meshes']) == names
          and all(type(mesh) is dict and mesh.get('skinned') is True for mesh in complete['meshes'].values()),
          'The worker bundle must contain every registered skinned Mesh.')
    _need(type(complete.get('objects')) is list and len(complete['objects']) == len(names) + 1
          and set(complete['objects']) == names | {complete['source_rig']}, 'The worker export object inventory changed.')
    review_name = plan.review_pin.path.name if plan.review_pin is not None else None
    enable_name = plan.forearm_enable_review_pin.path.name if plan.forearm_enable_review_pin is not None else None
    source_name = Path(model_file).with_suffix('.rig-source.json').name
    manifest_files = complete.get('files')
    expected_files = {name: digest for name, digest in hashes.items() if name not in {manifest_name, review_name, source_name, enable_name}}
    _need(type(manifest_files) is dict and manifest_files == expected_files,
          'The complete manifest does not own the exact worker payload inventory.')
    _need(next(pin.size for name, pin in pins if name == model_file) > 32, 'The worker FBX is incomplete.')
    sidecar_name = Path(model_file).with_suffix('.hair-motion.json').name
    forearm_name = Path(model_file).with_suffix('.forearm.json').name
    _need(sidecar_name in hashes and forearm_name in hashes
          and complete.get('hair_motion', {}).get('active') is True
          and complete['hair_motion'].get('file') == sidecar_name
          and complete.get('forearm_correction', {}).get('file') == forearm_name,
          'Retain the active Hair and matching Forearm companions.')
    old, old_pin = _read_hair(plan.base.model_file.with_suffix('.hair-motion.json'), plan.base.model_file, plan.base.asset_id)
    new, new_pin = _read_hair(stage / sidecar_name, stage / model_file, plan.base.asset_id)
    _structure(plan, old, new, formal, complete, hashes[model_file], complete_pin.sha256)
    rest_names = set(_rest_index(complete, hashes[model_file]))
    rigs = complete.get('rigs')
    _need(type(rigs) is dict and set(rigs) == {complete['source_rig']}
          and type(rigs[complete['source_rig']]) is list and len(rigs[complete['source_rig']]) == len(rest_names)
          and set(rigs[complete['source_rig']]) == rest_names, 'The complete Rig and Rest bone inventories differ.')
    forearm, _ = base._read_json(stage / forearm_name)
    _need(forearm.get('schema') == 'cdesigner.forearm/1' and forearm.get('fbx') == model_file
          and forearm.get('fbxSha256') == hashes[model_file] and type(forearm.get('meshes')) is list
          and bool(forearm['meshes']) and all(type(mesh) is dict and mesh.get('objectName') in names
              and type(mesh.get('sources')) is list and bool(mesh['sources']) for mesh in forearm['meshes']),
          'The worker omitted its nonempty FBX-bound Forearm calibration.')
    metadata = {'schema': 'cdesigner.rig-update/1', 'requested_mesh_ids': list(plan.requested_mesh_ids),
        'dependency_mesh_ids': list(plan.dependency_mesh_ids), 'required_sidecars': [forearm_name, sidecar_name],
        'hair_structure': {'schema': HAIR_SCHEMA, 'source_uid': plan.hair_source_uid, 'hair_part_id': plan.hair_part_id,
            'baseline_hair_sidecar_sha256': old_pin.sha256, 'candidate_hair_sidecar_sha256': new_pin.sha256}}
    if plan.review_pin is not None:
        _need(hashes.get(review_name) == plan.review_pin.sha256, 'The staged reviewed Rest permission differs.')
        metadata['reviewed_rest'] = {'schema': REVIEW_SCHEMA, 'file': review_name, 'sha256': plan.review_pin.sha256}
    if plan.forearm_enable_review_pin is not None:
        _need(hashes.get(enable_name) == plan.forearm_enable_review_pin.sha256, 'The staged Forearm permission differs.')
        enable = json.loads(plan.forearm_enable_review_json)
        _need(hashes.get(forearm_name) == enable['candidate_forearm_sha256'], 'The staged Forearm file differs from its exact permission.')
        metadata['reviewed_forearm_enable'] = {'schema': FOREARM_ENABLE_SCHEMA, 'file': enable_name,
            'sha256': plan.forearm_enable_review_pin.sha256}
    _need(hashes.get(source_name) == plan.source_snapshot_pin.sha256, 'The staged native source snapshot differs.')
    metadata['source_snapshot'] = {'schema': 'cdesigner.rig-source/1', 'file': source_name,
                                   'sha256': plan.source_snapshot_pin.sha256}
    return pins, hashes, metadata


def publish_candidate(plan, *, stage, model_file, files, parts, update_id):
    """Publish only an immutable reviewed Rig candidate, never a formal model."""
    revalidate_plan(plan)
    stage = base._guard_path(base._absolute(stage, 'Worker stage'))
    _need(base._io(stage).is_dir() and not base._within(stage, plan.base.unity_project)
          and not base._same(stage, plan.base.unity_project), 'Use a private worker stage outside Unity.')
    _need(type(files) in (list, tuple), 'Declare the complete worker file list.')
    files = list(files)
    if plan.review_pin is not None:
        name = plan.review_pin.path.name
        destination = base._owned(stage, name, payload=True)
        if os.path.lexists(base._io(destination)):
            _need(base._pin(destination).sha256 == plan.review_pin.sha256, 'A different staged review already exists.')
        else:
            base._copy_file(plan.review_pin, destination)
        if name not in files:
            files.append(name)
    if plan.forearm_enable_review_pin is not None:
        name = plan.forearm_enable_review_pin.path.name
        destination = base._owned(stage, name, payload=True)
        if os.path.lexists(base._io(destination)):
            _need(base._pin(destination).sha256 == plan.forearm_enable_review_pin.sha256, 'A different staged Forearm review already exists.')
        else:
            base._copy_file(plan.forearm_enable_review_pin, destination)
        if name not in files:
            files.append(name)
    source_name = Path(model_file).with_suffix('.rig-source.json').name
    source_path = base._owned(stage, source_name, payload=True)
    if os.path.lexists(base._io(source_path)):
        _need(base._pin(source_path).sha256 == plan.source_snapshot_pin.sha256, 'A different staged native source snapshot already exists.')
    else:
        base._copy_file(plan.source_snapshot_pin, source_path)
    if source_name not in files:
        files.append(source_name)
    pins, hashes, metadata = _bundle(plan, stage, model_file, files, parts)
    record = base.build_candidate(plan.base, model_file=model_file, file_hashes=hashes, parts=parts,
        mesh_ids=[part.part_id for part in plan.base.parts], update_rig=True, update_id=update_id)
    record['rig_update'] = metadata
    _need(len(base._json_bytes(record)) <= base.MAX_JSON_BYTES, 'The complete Rig candidate is oversized.')
    inbox = base._guard_path(plan.base.unity_project / '.codex/character-update-inbox' / plan.base.asset_id)
    final = base._guard_path(inbox / record['update_id'])
    _need(not os.path.lexists(base._io(final)), 'This immutable update identity already exists.')
    revalidate_plan(plan)
    base._io(inbox).mkdir(parents=True, exist_ok=True)
    base._guard_path(inbox)
    pending = base._absolute(tempfile.mkdtemp(prefix='.pending-' + record['update_id'] + '-', dir=base._io(inbox)), 'Owned pending candidate')
    owned = pending
    try:
        for relative, pin in pins:
            base._copy_file(pin, pending / relative)
        marker = '.completed-candidate.json.pending'
        base._write_file(pending / marker, base._json_bytes(record))
        for _relative, pin in pins:
            base._verify(pin)
        revalidate_plan(plan)
        base._guard_path(inbox)
        base._rename_noreplace(pending, final)
        owned = final
        revalidate_plan(plan)
        for relative, pin in pins:
            _need(base._pin(final / relative).sha256 == pin.sha256, 'A copied Rig payload changed before publication.')
        base._rename_noreplace(final / marker, final / 'candidate.json')
        owned = None
        return final / 'candidate.json'
    except Exception:
        base._cleanup_owned(owned, inbox)
        raise
