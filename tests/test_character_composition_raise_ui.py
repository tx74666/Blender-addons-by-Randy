"""Cheap NEW Raise UI receipt/clock/protection admission; no bpy or processes."""
import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load('run_character_composition_raise_ui')
helper = load('composition_raise_ui_acceptance')


def fixture(directory):
    base = Path(directory).resolve()
    pins = lambda name: {'path': str(base / name), 'sha256': 'a' * 64}
    config = {'schema': runner.SCHEMA, 'driverSha256': 'a' * 64,
        'raiseStageConfig': pins('raise-config.json'), 'raiseStageReport': pins('raise-report.json'),
        'evidenceDirectory': str(base / 'headful'), 'poseTolerance': 2e-5,
        'tools': {name: pins(name + '.py') for name in runner.TOOLS}}
    config['headfulCheckpoints'] = {'schema': 'x.validation.character-composition-ui-checkpoints/1',
        'enabled': True, 'helper': config['tools']['composition_ui_checkpoints'],
        'settleSeconds': 1.5, 'timeoutSeconds': 600.}
    ids = [str(uuid.UUID(str(index) * 32)) for index in range(1, 6)]
    spec = {'timeMode': 'Raise', 'sampleStart': .2, 'sampleEnd': 1.2, 'fps': 30.,
        'editSpeed': 1.7, 'editPhase': .17, 'referenceSpeed': .8, 'referencePhase': .07,
        'editWorkItemId': ids[0], 'editNewActionId': ids[1], 'holdWorkItemId': ids[2], 'holdNewActionId': ''}
    actual = {'work_item_id': ids[0], 'new_action_id': ids[1],
        'native_row_work_id': uuid.UUID(ids[0]).hex, 'native_row_new_action_id': uuid.UUID(ids[1]).hex,
        'item_id': uuid.UUID(ids[3]).hex, 'manifest_path': str(base / 'new_actions' / 'new_animation.json'), 'slot': 7}
    values, proof = {'timesSeconds': [.2, 1.2], 'channels': [['actual/path|localPosition|0', [0., .1]]]}, {'proof': 'actual-values'}
    physical = {'schema': 'x.validation.character-composition-sync/1', 'completed': True, 'kind': 'NEW',
        'actual_fbx_and_metadata_hashes_verified': True, 'physical_values_equal_independent_foreground': True,
        'actualWorkItemId': actual['native_row_work_id'], 'newActionId': actual['native_row_new_action_id'],
        'publicManifestWorkItemId': ids[0], 'publicManifestNewActionId': ids[1],
        'sourceActionSlot': 7, 'manifestPath': actual['manifest_path'], 'manifest_after_sha256': 'b' * 64,
        'fbx_file': str(base / 'raise.fbx'), 'fbx_sha256': 'c' * 64,
        'metadata_file': str(base / 'raise.json'), 'metadata_sha256': 'd' * 64, 'revision': 1,
        'spec': spec, 'effective_values': values, 'composition_export': proof,
        'receipt': {'composition_metadata': {'composition': spec}}}
    numeric_proof = {'completed': True, 'spec': spec, 'effective_values': values, 'composition_export': proof,
        'provenance': {'actualWorkItemId': actual['native_row_work_id'], 'newActionId': actual['native_row_new_action_id']}}
    stage_config = {'outputSeedFile': str(base / 'second-stage' / 'new_raise.blend'),
        'evidenceDirectory': str(base / 'second-stage'), 'inputBlend': str(base / 'artist-copy.blend'),
        'seedBlend': str(base / 'first-seed.blend'), 'seedBlendSha256': 'e' * 64,
        'sceneName': 'Composition QA · Fresh Workspace', 'rigName': 'CoshaRig',
        'targetGuid': 'f' * 32, 'modelSha256': 'f' * 64, 'freshWorkspace': pins('workspace.json')}
    numeric = {'schema': 'x.validation.character-composition-native/1', 'completed': True,
        'numeric_completed': True, 'fresh_workspace_cleanup': 'historical_native_state_and_rows_restored',
        'qa_seed': {'sceneName': stage_config['sceneName'], 'rigName': 'CoshaRig',
        'rigSceneNames': [stage_config['sceneName'], 'Scene'], 'targetGuid': 'f' * 32, 'modelSha256': 'f' * 64,
        'workspacePath': stage_config['freshWorkspace']['path'], 'seedBlend': stage_config['seedBlend'],
        'seedBlendSha256': 'e' * 64}}
    stage = {'schema': 'x.validation.character-composition-raise-stage-report/1', 'completed': True,
        'config': config['raiseStageConfig']['path'], 'output_seed': {'path': stage_config['outputSeedFile'], 'sha256': '1' * 64},
        'original_actions_unchanged': True, 'original_native_state_unchanged': True,
        'protected_original_assets_raw_exact': True, 'only_actual_registered_new_manifest_published': True,
        'immutable_inputs_unchanged': True, 'source_links_published': False, 'artist_save_performed': False,
        'seed_reloaded_during_execution': False, 'native_count': 217,
        'catalog_extension': {'workspace_identity_equal': True},
        'raise_authoring': {'complete': True, 'genuine_official_NewAction_registration': True,
            'protected_Customs_Sources_packets_unchanged': True, 'actual_NEW_identity': actual,
            'authoring': {'native_FK_transform_and_keys': True}},
        'timing': {'complete': True, 'raise_numeric_proof': numeric_proof}, 'physical': physical}
    return config, stage_config, stage, numeric


class RaiseUiTests(unittest.TestCase):
    def test_actual_D_public_N_native_completed_NEW_is_admitted_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            values = fixture(directory)
            before = copy.deepcopy(values)
            runner.validate_config(values[0])
            proof = runner.validate_reports(*values)
            self.assertEqual(proof['publicWorkItemId'], values[2]['physical']['publicManifestWorkItemId'])
            self.assertNotEqual(proof['publicWorkItemId'], proof['nativeWorkItemId'])
            self.assertEqual(proof['workspacePath'], values[1]['freshWorkspace']['path'])
            self.assertEqual(values, before)

    def test_first_current_admission_requires_exact_both_workspace_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            admission = runner.validate_reports(*fixture(directory))
            admission['workspaceIdentity'] = 'actual immutable workspace identity'
            rig = NS(name=admission['rigName'], users_scene=[NS(name=name) for name in admission['rigSceneNames']])
            item = NS(rig=rig, new_animation=True, item_id=admission['nativeItemId'],
                work_item_id=admission['nativeWorkItemId'], new_action_id=admission['nativeNewActionId'],
                custom_slot=admission['slot'])
            saved = NS(rig=rig, workspace_path=admission['workspacePath'],
                composition_workspace_path=admission['workspacePath'],
                workspace_identity=admission['workspaceIdentity'],
                composition_workspace_identity=admission['workspaceIdentity'])
            context = NS(window=NS(), scene=NS(name=admission['sceneName'], objects={'CoshaRig': rig}))
            modules = {'animation_worklist': NS(state=lambda _context:saved, _item=lambda _saved,_id:item)}
            with patch.dict('sys.modules', {'bpy': NS(context=context)}):
                self.assertEqual(helper._current(admission['nativeItemId'], admission, modules), (context,saved,item))
                for field in ('workspace_identity', 'composition_workspace_identity'):
                    setattr(saved, field, 'foreign identity')
                    with self.assertRaisesRegex(AssertionError, 'workspace/Composition Library identity'):
                        helper._current(admission['nativeItemId'], admission, modules)
                    setattr(saved, field, admission['workspaceIdentity'])

    def test_missing_or_alias_Raise_output_seed_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in ('missing', 'first-stage', 'artist'):
                values = fixture(directory)
                if mode == 'missing':
                    values[2].pop('output_seed')
                else:
                    path = values[1]['seedBlend' if mode == 'first-stage' else 'inputBlend']
                    values[1]['outputSeedFile'] = values[2]['output_seed']['path'] = path
                with self.assertRaises(AssertionError):
                    runner.validate_reports(*values)

    def test_actual_collected_following_Loop_revision_and_ID_are_bound_to_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = fixture(directory)[2]['physical']['spec']
            publication = {'manifestPath': str(Path(directory) / 'loop-link.json'),
                'fbxPath': str(Path(directory) / 'loop.fbx'), 'metadataPath': str(Path(directory) / 'loop.json'),
                'manifestSha256': 'a' * 64, 'fbxSha256': 'b' * 64, 'metadataSha256': 'c' * 64, 'revision': 3}
            author = {'followingHoldPublication': publication, 'collectedHoldLoopWorkItemId': spec['holdWorkItemId']}
            timing = {'config': {'followingHoldPublication': publication,
                'followingHoldWorkItemId': spec['holdWorkItemId'], 'holdLoopWorkItemId': spec['holdWorkItemId'],
                'raiseWorkItemId': spec['editWorkItemId']}, 'phases': [{
                'phase': 'prove_actual_collected_following_Hold', 'complete_numeric_payload_equal': True,
                'no_unsynced_local_Hold_substitution': True, 'revision': 3,
                'manifest_sha256': 'a' * 64, 'fbx_sha256': 'b' * 64, 'metadata_sha256': 'c' * 64}]}
            self.assertEqual(runner.validate_following_publication(spec, timing, author), publication)
            for key, changed in (('revision', 2), ('manifest_sha256', 'd' * 64),
                ('complete_numeric_payload_equal', False), ('no_unsynced_local_Hold_substitution', False)):
                bad = copy.deepcopy(timing)
                bad['phases'][0][key] = changed
                with self.assertRaises(AssertionError): runner.validate_following_publication(spec, bad, author)
            bad = copy.deepcopy(timing)
            bad['config']['holdLoopWorkItemId'] = str(uuid.UUID('5' * 32))
            with self.assertRaises(AssertionError): runner.validate_following_publication(spec, bad, author)
            bad = copy.deepcopy(author)
            bad['followingHoldPublication']['fbxSha256'] = 'd' * 64
            with self.assertRaises(AssertionError): runner.validate_following_publication(spec, timing, bad)

    def test_failed_timing_physical_or_original_protection_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            for group, key in ((None, 'original_actions_unchanged'), (None, 'protected_original_assets_raw_exact'),
                (None, 'immutable_inputs_unchanged'), ('timing', 'complete'), ('physical', 'completed'),
                ('physical', 'actual_fbx_and_metadata_hashes_verified'),
                ('physical', 'physical_values_equal_independent_foreground')):
                values = fixture(directory)
                (values[2][group] if group else values[2])[key] = False
                with self.assertRaises(AssertionError):
                    runner.validate_reports(*values)

    def test_SOURCE_as_NEW_or_different_public_id_native_slot_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, changed in (('kind', 'SOURCE'), ('publicManifestWorkItemId', '5' * 32),
                ('publicManifestNewActionId', '5' * 32), ('sourceActionSlot', 8)):
                values = fixture(directory)
                values[2]['physical'][key] = changed
                with self.assertRaises(AssertionError):
                    runner.validate_reports(*values)

    def test_malformed_and_zero_real_ids_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ('not-an-id', '0' * 32):
                values = fixture(directory)
                values[2]['raise_authoring']['actual_NEW_identity']['item_id'] = value
                with self.assertRaises(AssertionError):
                    runner.validate_reports(*values)

    def test_current_model_owner_workspace_and_memberships_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, changed in (('targetGuid', 'a' * 32), ('modelSha256', 'a' * 64),
                ('sceneName', 'Artist Scene'), ('rigSceneNames', ['Scene'])):
                values = fixture(directory)
                values[3]['qa_seed'][key] = changed
                with self.assertRaises(AssertionError):
                    runner.validate_reports(*values)

    def test_actual_published_numeric_and_native_receipt_equal_required(self):
        with tempfile.TemporaryDirectory() as directory:
            for key in ('effective_values', 'composition_export', 'spec'):
                values = copy.deepcopy(fixture(directory))
                values[2]['timing']['raise_numeric_proof'][key] = {'other': True}
                with self.assertRaises(AssertionError):
                    runner.validate_reports(*values)

    def test_old_prepublication_hash_and_invalid_revision_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, value in (('manifest_after_sha256', ''), ('revision', 0), ('fbx_sha256', 'UPPERCASE')):
                values = fixture(directory)
                values[2]['physical'][key] = value
                with self.assertRaises(AssertionError):
                    runner.validate_reports(*values)

    def test_headful_is_explicit_and_helpers_cannot_be_mutable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in ('disabled', 'unknown', 'inside-evidence', 'nonfinite'):
                config = fixture(directory)[0]
                if mode == 'disabled': config['headfulCheckpoints']['enabled'] = False
                elif mode == 'unknown': config['allowArtist'] = True
                elif mode == 'nonfinite': config['headfulCheckpoints']['settleSeconds'] = float('nan')
                else: config['raiseStageReport']['path'] = str(Path(config['evidenceDirectory']) / 'proof.json')
                with self.assertRaises(AssertionError): runner.validate_config(config)
            config = fixture(directory)[0]
            config['quitOnCompletion'] = True
            runner.validate_config(config)
            config['quitOnCompletion'] = 'close any Blender'
            with self.assertRaises(AssertionError): runner.validate_config(config)

    def test_independent_real_raise_clock_applies_phase_speed_once_and_uses_local_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = fixture(directory)[2]['physical']['spec']
            result = helper.checkpoint_times(spec)
            self.assertAlmostEqual(result['transition_seconds'], .83 / 1.7)
            self.assertAlmostEqual(result['B_local_seconds'][1], .7)
            self.assertAlmostEqual(result['C_frames'][1], 1 + (.33 / 1.7) * 30)
            self.assertTrue(all(seconds < result['transition_seconds'] for seconds in result['C_seconds']))

    def test_hold_disguised_raise_invalid_phase_and_nonfinite_clock_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, value in (('timeMode', 'HoldPose'), ('editPhase', .75), ('editSpeed', 0),
                ('fps', float('nan')), ('sampleEnd', .2)):
                spec = copy.deepcopy(fixture(directory)[2]['physical']['spec'])
                spec[key] = value
                with self.assertRaises(AssertionError): helper.checkpoint_times(spec)

    def test_claim_evidence_retains_previous_screenshots_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'headful'
            roots = [evidence / 'config', evidence / 'datafiles']
            roots[0].mkdir(parents=True)
            roots[1].mkdir()
            self.assertEqual(runner.claim_evidence(evidence, roots), evidence.resolve())
            (evidence / 'old.png').write_bytes(b'prior native evidence')
            with self.assertRaises(AssertionError): runner.claim_evidence(evidence, roots)

    def test_portable_Action_guard_accepts_exact_reopened_ID_but_rejects_changed_slot_contents(self):
        action = lambda value: NS(name='Actual NEW Raise', library=None,
            slots=[NS(handle=7)], contents=value)
        bpy = NS(data=NS(actions=[action(.17)]))
        modules = {'animation_worklist_fingerprint': NS(fingerprint_action=lambda value, slot:
            {'fingerprint_known': True, 'action_fingerprint': [value.contents, slot]})}
        with patch.dict('sys.modules', {'bpy': bpy}):
            protected = helper.action_proofs(modules)
            bpy.data.actions = [action(.17)]  # actual reopen replaces native IDs
            helper.assert_actions(protected, modules)
            bpy.data.actions = [action(.19)]
            with self.assertRaisesRegex(AssertionError, 'protected prior Action'):
                helper.assert_actions(protected, modules)
            bpy.data.actions = []
            with self.assertRaises(AssertionError): helper.assert_actions(protected, modules)

    def test_unproved_existing_Action_does_not_silently_drop_from_protection(self):
        bpy = NS(data=NS(actions=[NS(name='Foreign unsupported Action', library=None, slots=[])]))
        modules = {'animation_worklist_fingerprint': NS(fingerprint_action=lambda _value, _slot:
            {'fingerprint_known': False})}
        with patch.dict('sys.modules', {'bpy': bpy}):
            with self.assertRaisesRegex(AssertionError, 'unproved content'):
                helper.action_proofs(modules)

    def _guard_fixture(self, active):
        class Rig(dict): pass
        rig = Rig()
        rig.data = 'actual motion data'
        rig.animation_data = NS(use_tweak_mode=active)
        rig['actual owned gun record'] = True
        scene = NS()
        context = NS(scene=scene)
        item = NS(rig=rig)
        saved = NS(target_guid='actual target', model_sha256='actual model')
        admission = {'targetGuid': saved.target_guid, 'modelSha256': saved.model_sha256,
            'spec': {'weaponContextSha256': 'c' * 64, 'weaponModelSha256': 'd' * 64}}
        target = NS(_load=lambda _rig:{'scene': scene, 'original_data': 'protected original data'},
            ensure_active=Mock())
        backend = NS(active=lambda _rig:active, validate=Mock(), item_spec=lambda _item:admission['spec'])
        ua = NS(_rest_state=lambda data:data)
        weapon = NS(RECORD='actual owned gun record', validate_owned=lambda _rig:({'proof_json':
            '{"context_sha256":"' + 'c' * 64 + '","source_sha256":"' + 'd' * 64 + '"}'}, []))
        modules = {'animation_worklist_target':target, 'animation_composition':backend,
            'unity_animation':ua, 'animation_composition_weapon':weapon}
        session = NS(visual_guard=Mock())
        rest = {'motion': rig.data, 'original': 'protected original data'}
        return (context,saved,item), admission, modules, session, rest

    def test_active_native_Tweak_uses_authenticated_Composition_guard_not_ordinary_Worklist_Action_gate(self):
        current, admission, modules, session, rest = self._guard_fixture(True)
        target, backend = modules['animation_worklist_target'], modules['animation_composition']
        target.ensure_active.side_effect = AssertionError('Ordinary target rejects native Tweak')
        with patch.object(helper, '_current', return_value=current), patch.object(helper, 'assert_actions') as actions:
            helper.guard('actual item ID', admission, modules, session, [], rest)
            target.ensure_active.assert_not_called()
            backend.validate.assert_called_once_with(current[0], current[2].rig)
            session.visual_guard.assert_called_once()
            actions.assert_called_once()
            backend.validate.side_effect = ValueError('Actual session ownership changed')
            with self.assertRaisesRegex(ValueError, 'session ownership changed'):
                helper.guard('actual item ID', admission, modules, session, [], rest)
            backend.validate.side_effect = None
            rest['motion'] = 'foreign motion Rest'
            with self.assertRaisesRegex(AssertionError, 'Armature Rest field changed'):
                helper.guard('actual item ID', admission, modules, session, [], rest)

    def test_inactive_checkpoint_retains_ordinary_strict_Worklist_guard(self):
        current, admission, modules, session, rest = self._guard_fixture(False)
        target, backend = modules['animation_worklist_target'], modules['animation_composition']
        with patch.object(helper, '_current', return_value=current), patch.object(helper, 'assert_actions'):
            helper.guard('actual item ID', admission, modules, session, [], rest)
            target.ensure_active.assert_called_once_with(current[0], current[2].rig)
            backend.validate.assert_not_called()
            target.ensure_active.side_effect = ValueError('Foreign ordinary Action')
            with self.assertRaisesRegex(ValueError, 'Foreign ordinary Action'):
                helper.guard('actual item ID', admission, modules, session, [], rest)


if __name__ == '__main__':
    unittest.main()
