"""Standalone UI runner admission only; no Blender/import/worker is launched."""
from copy import deepcopy
import ast
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1]/'tools/run_character_composition_ui_fixture.py'
SPEC = importlib.util.spec_from_file_location('standalone_composition_ui', PATH)
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)
SESSION_PATH = PATH.with_name('composition_session_acceptance.py')
SESSION_SPEC = importlib.util.spec_from_file_location('standalone_composition_session', SESSION_PATH)
session_helper = importlib.util.module_from_spec(SESSION_SPEC)
SESSION_SPEC.loader.exec_module(session_helper)


def actual_source_model_api():
    """Execute the canonical helper's real body, with only bpy path adapter."""
    path = PATH.parents[1] / 'addons/character_designer/animation_link_source.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'_sha256', '_source_model'}]
    namespace = {'Path': Path, 'hashlib': hashlib,
                 'bpy': NS(path=NS(abspath=lambda value: value)), '_error': ValueError}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    return NS(_source_model=namespace['_source_model'])


def config():
    return dict(schema=driver.SCHEMA, inputBlend='D:/qa/artist_final_validation.blend',
        inputBlendSha256='1'*64, addonPackage='D:/frozen/character_designer',
        runtimeManifest='D:/frozen/python_projection.json', runtimeManifestSha256='2'*64,
        evidenceDirectory='D:/qa/ui-new', numericReport='D:/qa/numeric/composition_native_report.json',
        numericReportSha256='3'*64, seedBlend='D:/qa/numeric/qa_seed.blend', seedBlendSha256='4'*64,
        workItemId='11111111-1111-1111-1111-111111111111', sceneName='Composition QA · Fresh Workspace',
        rigName='CoshaRig', targetGuid='a'*32, modelSha256='5'*64,
        sessionHelper={'path':'D:/tools/composition_session_acceptance.py','sha256':'6'*64},
        sessionConfig={'createAWorkCopy':True,'seekSeconds':[.137,.419],'poseTolerance':2e-5})


def proofs(value):
    ids = ['1'*32, '7'*32]
    native_ids = ['8'*32, '9'*32]
    seed = dict(schema=driver.SEED_SCHEMA, seedBlend=value['seedBlend'], seedBlendSha256=value['seedBlendSha256'],
        sceneName=value['sceneName'], rigName='CoshaRig', rigSceneNames=sorted([value['sceneName'],'Scene']),
        targetGuid=value['targetGuid'], modelSha256=value['modelSha256'], workspacePath='D:/prepared/character_animation_workspace.json',
        workspaceSha256='b'*64, workItemIds=ids, itemIds=native_ids,
        physicalReport='D:/qa/physical/composition_sync_report.json', physicalReportSha256='c'*64)
    numeric = dict(schema='x.validation.character-composition-native/1', completed=True, numeric_completed=True,
        same_b_across_two_a_seek_and_object_motion=True, fresh_native_associations_verified=True,
        protected_prior_actions_unchanged=True, protected_assets_unchanged=True,
        fresh_workspace_cleanup='historical_native_state_and_rows_restored',
        qa_seed=seed, fresh_packet_proofs=[{'work_item_id':item_id,'model_sha256':value['modelSha256']} for item_id in ids],
        fresh_workspace_scene={'name':value['sceneName'],'historical_rig_scene_names':['Scene']})
    physical = dict(schema='x.validation.character-composition-sync/1', completed=True,
        physical_values_equal_foreground_and_two_a=True, actual_fbx_and_metadata_hashes_verified=True,
        items=[{'actualWorkItemId':item_id,'completed':True,'actual_fbx_and_metadata_hashes_verified':True,
            'kind':'SOURCE','newActionId':'','modelSha256':value['modelSha256']} for item_id in ids])
    original = {key:value[key] for key in ('inputBlend','inputBlendSha256','addonPackage','runtimeManifest','runtimeManifestSha256')}
    original.update(sceneName='Scene',compositionModelSha256=value['modelSha256'])
    return numeric, physical, original


class SessionVisualGuardReceiptTests(unittest.TestCase):
    def _fixture(self, directory):
        class NoRawReceiptLookup:
            def __getitem__(self, key):
                raise AssertionError('The tool must use the official reference-receipt accessor.')
        model, cache = directory / 'model.fbx', directory / 'reference.blend'
        model.write_bytes(b'owned model')
        cache.write_bytes(b'owned protected reference')
        rig = NoRawReceiptLookup()
        item = NS(rig=rig, model_file=str(model), model_sha256=session_helper._sha(model), new_animation=False)
        base = NS(library=NS(filepath=str(cache)))
        full_sha = '0123456789abcdef' * 4
        session = dict(base_action=base, base_slot=7, base_hash='protected motion',
                       spec={'referencePacketSha256': full_sha})
        receipt = dict(cache=str(cache), cache_sha256=session_helper._sha(cache))
        backend = NS(_packet_for_item=Mock(return_value={}), active=Mock(return_value=True),
                     validate=Mock(return_value=(session,)), _motion_hash=Mock(return_value='protected motion'),
                     reference_receipt=Mock(return_value=receipt))
        modules = {'animation_composition': backend,
                   'animation_worklist': NS(_fresh_workspace=Mock(return_value={}), _check_baseline=Mock()),
                   'animation_link': NS(_sha256=session_helper._sha)}
        return item, session, receipt, modules, full_sha

    def _guard(self, item, modules):
        with patch.object(session_helper, '_modules', return_value=modules), \
             patch.object(session_helper, '_current', return_value=(object(), object(), item)), \
             patch.dict('sys.modules', {'bpy': NS(data=NS(scenes=[]), path=NS(abspath=lambda value: value))}):
            session_helper.visual_guard(object(), 'actual native item', {})

    def test_reference_accessor_receives_exact_rig_and_complete_sha(self):
        with tempfile.TemporaryDirectory() as folder:
            item, _session, _receipt, modules, full_sha = self._fixture(Path(folder))
            self._guard(item, modules)
            modules['animation_composition'].reference_receipt.assert_called_once_with(item.rig, full_sha)

    def test_missing_receipt_changed_cache_library_and_motion_remain_rejected(self):
        for damage, message in [('missing', 'receipt is missing'), ('cache', 'cache changed'),
                                ('library', 'cache changed'), ('motion', 'Readonly A changed')]:
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as folder:
                item, session, receipt, modules, _sha = self._fixture(Path(folder))
                backend = modules['animation_composition']
                if damage == 'missing': backend.reference_receipt.return_value = None
                elif damage == 'cache': Path(receipt['cache']).write_bytes(b'changed protected bytes')
                elif damage == 'library': session['base_action'].library.filepath = str(Path(folder) / 'foreign.blend')
                else: backend._motion_hash.return_value = 'changed readonly motion'
                with self.assertRaisesRegex(AssertionError, message): self._guard(item, modules)

    def test_malformed_or_wrong_full_sha_receipt_cannot_fall_back_to_raw_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            item, _session, _receipt, modules, _sha = self._fixture(Path(folder))
            modules['animation_composition'].reference_receipt.side_effect = ValueError('malformed or wrong full packet SHA')
            with self.assertRaisesRegex(ValueError, 'wrong full packet SHA'): self._guard(item, modules)


class SessionWorkCopyLifetimeTests(unittest.TestCase):
    def _exercise_create_a_growth(self, replace_b=False):
        class SnapshotReached(RuntimeError):
            pass
        b_id, a_id = '8' * 32, '9' * 32
        rig, b_action, a_action = object(), object(), object()
        old_b = NS(item_id=b_id, rig=rig, custom_action=b_action, custom_slot=4)
        fresh_b = NS(item_id=b_id, rig=rig, custom_action=b_action, custom_slot=4)
        for row in (old_b, fresh_b):
            for key in session_helper._B_ROW_FIELDS:
                if key != 'item_id': setattr(row, key, 'unchanged source-test ' + key)
            row.source_action, row.source_slot = a_action, 7
        a_item = NS(item_id=a_id, custom_action=a_action, custom_slot=7)
        context = NS(preferences=NS(edit=NS(use_global_undo=True)))
        saved = object()
        def create_copy(*_args):
            # Simulate the old collection wrapper becoming unreadable after
            # genuine Create A Work Copy appends another native row.
            old_b.custom_action, old_b.rig = None, None
            if replace_b:
                fresh_b.custom_action = object()
            return a_item
        def motion_hash(action, _slot):
            self.assertIn(action, (b_action, a_action))
            return 'authenticated source-test motion'
        def snapshot(_context, actual_rig):
            self.assertIs(actual_rig, rig)
            raise SnapshotReached('fresh rows reached baseline snapshot')
        backend = NS(active=Mock(return_value=False), item_spec=Mock(return_value={'timeMode':'HoldPose'}),
                     _motion_hash=Mock(side_effect=motion_hash))
        worklist = NS(_item=Mock(return_value=a_item))
        modules = {'animation_composition':backend, 'unity_animation':NS(_snapshot=Mock(side_effect=snapshot)),
                   'animation_worklist':worklist}
        current = Mock(side_effect=[(context, saved, old_b), (context, saved, fresh_b)])
        with patch.object(session_helper, '_evidence', return_value={'report':Path('D:/unused/report.json')}), \
             patch.object(session_helper, '_modules', return_value=modules), \
             patch.object(session_helper, '_current', current), \
             patch.object(session_helper, '_work_copy', side_effect=create_copy), \
             patch.object(session_helper, '_write'), \
             patch.dict('sys.modules', {'bpy':NS(app=NS(version_string='source-test-only'),context=context)}):
            if replace_b:
                with self.assertRaisesRegex(AssertionError, 'original B Action'):
                    next(session_helper.run_steps(context, b_id, {}))
                modules['unity_animation']._snapshot.assert_not_called()
            else:
                with self.assertRaisesRegex(SnapshotReached, 'fresh rows'):
                    next(session_helper.run_steps(context, b_id, {}))
        self.assertEqual(current.call_count, 2)
        current.assert_called_with(b_id, modules)
        worklist._item.assert_called_once_with(saved, a_id)
        self.assertTrue(context.preferences.edit.use_global_undo)

    def test_create_a_growth_reacquires_b_and_a_before_baseline_snapshot(self):
        self._exercise_create_a_growth()

    def test_coherent_b_replacement_during_create_a_cannot_reset_baseline(self):
        self._exercise_create_a_growth(replace_b=True)

    def test_actual_current_rejects_missing_and_duplicate_b_ids(self):
        for count in (0, 2):
            with self.subTest(count=count):
                saved = NS(items=[NS(item_id='8' * 32) for _ in range(count)])
                worklist = NS(state=Mock(return_value=saved), _item=Mock())
                with patch.dict('sys.modules', {'bpy':NS(context=object())}):
                    with self.assertRaisesRegex(AssertionError, 'missing or ambiguous'):
                        session_helper._current('8' * 32, {'animation_worklist':worklist})
                worklist._item.assert_not_called()


class UiFixtureAdmissionTests(unittest.TestCase):
    def test_actual_native_activation_uses_plain_id_six_arg_proof_and_fresh_row(self):
        context, rig, links = object(), object(), object()
        native_id = '8' * 32
        old_item, fresh_item = NS(item_id=native_id), NS(item_id=native_id)
        def activate(_context, identity, side):
            self.assertEqual((identity, side), (native_id, 'CUSTOM'))
            old_item.item_id = ''
        worklist = NS(activate=Mock(side_effect=activate))
        proof = {'native_link': 'source-test proof only'}
        fingerprint, anchor = object(), object()
        native = NS(fresh_association_proof=Mock(return_value=proof),
                    reacquire_work_item=Mock(return_value=fresh_item), capture_import_anchor=Mock(return_value=anchor))
        result = driver._activate_selected(context, rig, old_item, links, worklist, native, 'a' * 64, fingerprint)
        self.assertEqual(result[:2], (native_id, proof))
        self.assertIs(result[2], fresh_item)
        native.capture_import_anchor.assert_called_once_with(context, rig, native_id, worklist, fingerprint)
        native.fresh_association_proof.assert_called_once_with(context, rig, native_id, links, worklist, 'a' * 64,
                                                             anchor=anchor, fingerprint=fingerprint)
        native.reacquire_work_item.assert_called_once_with(context, rig, native_id, worklist,
                                                         anchor=anchor, fingerprint=fingerprint)

    def test_failed_native_activation_proof_has_no_old_api_or_identity_fallback(self):
        context, rig, links = object(), object(), object()
        item, worklist = NS(item_id='8' * 32), NS(activate=Mock())
        native = NS(fresh_association_proof=Mock(side_effect=ValueError('changed actual association')),
                    reacquire_work_item=Mock(), capture_import_anchor=Mock(return_value=object()))
        with self.assertRaisesRegex(ValueError, 'changed actual association'):
            driver._activate_selected(context, rig, item, links, worklist, native, 'a' * 64, object())
        native.reacquire_work_item.assert_not_called()
        worklist.activate.reset_mock(); item.item_id = ''
        with self.assertRaises((AssertionError, ValueError)):
            driver._activate_selected(context, rig, item, links, worklist, native, 'a' * 64, object())
        worklist.activate.assert_not_called()

    def test_normal_quit_is_explicit_boolean_and_headful_only(self):
        value = config(); value['quitOnCompletion'] = False
        driver.validate_fixture(value)
        for setting in (True,1,'true'):
            value['quitOnCompletion'] = setting
            with self.assertRaises(AssertionError): driver.validate_fixture(value)
        value['quitOnCompletion'] = True
        value['sessionConfig']['headfulCheckpoints'] = dict(
            schema='x.validation.character-composition-ui-checkpoints/1',enabled=True,
            helper={'path':'D:/tools/composition_ui_checkpoints.py','sha256':'7'*64},
            settleSeconds=2.,timeoutSeconds=1200.)
        self.assertIs(driver.validate_fixture(value),value)

    def test_opt_in_headful_options_require_pinned_helper_and_bounded_timing(self):
        value = config()
        options = dict(schema='x.validation.character-composition-ui-checkpoints/1', enabled=True,
            helper={'path':'D:/tools/composition_ui_checkpoints.py','sha256':'7'*64},
            settleSeconds=2., timeoutSeconds=1200.)
        value['sessionConfig']['headfulCheckpoints'] = options
        driver.validate_fixture(value)
        for key, replacement in [('enabled',False),('settleSeconds',0),('timeoutSeconds',float('nan'))]:
            changed = deepcopy(value); changed['sessionConfig']['headfulCheckpoints'][key] = replacement
            with self.assertRaises(AssertionError): driver.validate_fixture(changed)
        changed = deepcopy(value)
        changed['sessionConfig']['headfulCheckpoints']['helper']['path'] = value['evidenceDirectory']+'/helper.py'
        with self.assertRaises(AssertionError): driver.validate_fixture(changed)

    def test_headful_profile_must_be_owned_and_empty_at_startup(self):
        with tempfile.TemporaryDirectory() as parent:
            value = config(); evidence = Path(parent)/'ui'; value['evidenceDirectory'] = str(evidence)
            value['sessionConfig']['headfulCheckpoints'] = {'enabled': True}
            profile, cache = evidence/'profile', evidence/'datafiles'
            profile.mkdir(parents=True); cache.mkdir()
            with patch.dict(driver.os.environ, {'BLENDER_USER_CONFIG':str(profile)}):
                self.assertEqual(driver.claim_evidence(value,cache),evidence)
                (profile/'foreign_preferences.blend').write_bytes(b'prior profile')
                with self.assertRaises(AssertionError): driver.claim_evidence(value,cache)

    def test_actual_source_model_Path_digest_tuple_is_unpacked_before_pin(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'actual_character.fbx'
            model.write_bytes(b'actual isolated model bytes')
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            link = {'modelFile': str(model), 'modelSha256': digest}
            calls = []
            result = driver._pin_model(actual_source_model_api(), link, str(model), digest,
                                       lambda path, sha: calls.append((path, sha)))
            self.assertEqual(result, model.resolve())
            self.assertEqual(calls, [(model.resolve(), digest)])
            self.assertIsInstance(calls[0][0], Path)

    def test_actual_source_model_digest_mismatch_rejects_before_UI_pin(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'actual_character.fbx'
            model.write_bytes(b'actual isolated model bytes')
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            calls = []
            with self.assertRaisesRegex(AssertionError, 'linked model digest'):
                driver._pin_model(actual_source_model_api(), {'modelFile': str(model), 'modelSha256': digest},
                                  str(model), '0' * 64, lambda *args: calls.append(args))
            self.assertEqual(calls, [])

    def test_actual_source_model_changed_bytes_remain_strictly_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'actual_character.fbx'
            model.write_bytes(b'before')
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            model.write_bytes(b'changed')
            calls = []
            with self.assertRaisesRegex(ValueError, 'model FBX changed'):
                driver._pin_model(actual_source_model_api(), {'modelFile': str(model), 'modelSha256': digest},
                                  str(model), digest, lambda *args: calls.append(args))
            self.assertEqual(calls, [])

    def test_complete_pinned_fixture_and_exact_tested_native_id_accept(self):
        value = config()
        self.assertIs(driver.validate_fixture(value), value)
        numeric, physical, original = proofs(value)
        seed, native_id = driver.validate_reports(value,numeric,physical,original)
        self.assertIs(seed,numeric['qa_seed'])
        self.assertEqual(native_id,'8'*32)

    def test_second_real_work_id_selects_its_own_native_id(self):
        value = config(); value['workItemId'] = '7'*32
        self.assertEqual(driver.validate_reports(value,*proofs(value))[1],'9'*32)

    def test_artist_or_same_input_seed_reject(self):
        for key,new in [('inputBlend','D:/Blender/Projects/Character/X/X.blend'),
                        ('seedBlend',config()['inputBlend'])]:
            value = config(); value[key] = new
            with self.assertRaises(AssertionError): driver.validate_fixture(value)

    def test_mutable_evidence_cannot_contain_seed_runtime_or_helper(self):
        for key in ('seedBlend','inputBlend','runtimeManifest','addonPackage','numericReport'):
            value = config(); value[key] = value['evidenceDirectory']+'/owned/'+Path(value[key]).name
            with self.assertRaises(AssertionError): driver.validate_fixture(value)
        value = config(); value['sessionHelper']['path'] = value['evidenceDirectory']+'/helper.py'
        with self.assertRaises(AssertionError): driver.validate_fixture(value)

    def test_relative_paths_missing_hash_or_invented_display_id_reject(self):
        for key,new in [('seedBlend','seed.blend'),('seedBlendSha256','unrecorded'),
                        ('workItemId','Walk_N'),('rigName','CoshaRig.001')]:
            value = config(); value[key] = new
            with self.assertRaises(AssertionError): driver.validate_fixture(value)

    def test_unknown_output_or_publication_options_reject(self):
        value = config(); value['allowPreparedLinkPublication'] = True
        with self.assertRaises(AssertionError): driver.validate_fixture(value)
        for key in ('evidenceDirectory','addonModule','sync'):
            value = config(); value['sessionConfig'][key] = 'foreign'
            with self.assertRaises(AssertionError): driver.validate_fixture(value)

    def test_nonfinite_or_weak_pose_and_seek_options_reject(self):
        for key,new in [('seekSeconds',[.1,float('nan')]),('seekSeconds',[.1,.1]),
                        ('poseTolerance',.001),('poseTolerance',True),('createAWorkCopy','yes')]:
            value = config(); value['sessionConfig'][key] = new
            with self.assertRaises(AssertionError): driver.validate_fixture(value)

    def test_optional_a_selection_requires_actual_uuid_and_control_name(self):
        value = config(); value['sessionConfig'].update(aWorkItemId='d'*32,rotationControl='hand.L')
        driver.validate_fixture(value)
        for key,new in [('aWorkItemId','A Walk'),('rotationControl','')]:
            changed = deepcopy(value); changed['sessionConfig'][key] = new
            with self.assertRaises(AssertionError): driver.validate_fixture(changed)

    def test_numeric_or_physical_pending_proof_reject(self):
        for target,key in [('numeric','completed'),('numeric','numeric_completed'),
                           ('physical','completed'),('physical','physical_values_equal_foreground_and_two_a'),
                           ('physical','actual_fbx_and_metadata_hashes_verified')]:
            value = config(); numeric,physical,original = proofs(value)
            (numeric if target=='numeric' else physical)[key] = False
            with self.assertRaises(AssertionError): driver.validate_reports(value,numeric,physical,original)

    def test_seed_work_ids_must_equal_both_actual_successful_proofs(self):
        for target in ('numeric','physical','seed'):
            value = config(); numeric,physical,original = proofs(value)
            if target=='numeric': numeric['fresh_packet_proofs'][0]['work_item_id'] = 'd'*32
            elif target=='physical': physical['items'][0]['actualWorkItemId'] = 'd'*32
            else: numeric['qa_seed']['workItemIds'][0] = 'd'*32
            with self.assertRaises(AssertionError): driver.validate_reports(value,numeric,physical,original)

    def test_untested_work_item_and_duplicate_native_receipt_id_reject(self):
        value = config(); value['workItemId'] = 'd'*32
        with self.assertRaises(AssertionError): driver.validate_reports(value,*proofs(value))
        value = config(); numeric,physical,original = proofs(value)
        numeric['qa_seed']['itemIds'][1] = numeric['qa_seed']['itemIds'][0]
        with self.assertRaises(AssertionError): driver.validate_reports(value,numeric,physical,original)

    def test_seed_model_original_input_and_projection_must_remain_exact(self):
        for key,new in [('modelSha256','d'*64),('inputBlendSha256','d'*64),
                        ('runtimeManifestSha256','d'*64),('sceneName','Another Scene')]:
            value = config(); numeric,physical,original = proofs(value); value[key] = new
            with self.assertRaises(AssertionError): driver.validate_reports(value,numeric,physical,original)

    def test_original_and_official_qa_memberships_are_preserved_exactly(self):
        value = config(); numeric,physical,original = proofs(value)
        for owners in [[value['sceneName']],[value['sceneName'],'Scene','Foreign'],['Scene']]:
            numeric['qa_seed']['rigSceneNames'] = owners
            with self.assertRaises(AssertionError): driver.validate_reports(value,numeric,physical,original)

    def test_all_pinned_historical_scene_memberships_are_retained(self):
        value = config(); numeric,physical,original = proofs(value)
        numeric['fresh_workspace_scene']['historical_rig_scene_names'].append('Another Authenticated Original Scene')
        numeric['qa_seed']['rigSceneNames'] = sorted([*numeric['fresh_workspace_scene']['historical_rig_scene_names'],value['sceneName']])
        driver.validate_reports(value,numeric,physical,original)

    def test_native_row_requires_unique_actual_ids_and_exact_rig(self):
        rig = object()
        row = NS(work_item_id='1'*32,item_id='8'*32,rig=rig,new_animation=False)
        saved = NS(items=[row])
        self.assertIs(driver.select_native_row(saved,rig,'1'*32,'8'*32),row)
        for changes in [{'item_id':'9'*32},{'rig':object()},{'new_animation':True}]:
            invalid = NS(**{**vars(row),**changes})
            with self.assertRaises(AssertionError): driver.select_native_row(NS(items=[invalid]),rig,'1'*32,'8'*32)
        with self.assertRaises(AssertionError): driver.select_native_row(NS(items=[row,row]),rig,'1'*32,'8'*32)

    def test_new_empty_evidence_and_owned_startup_cache_accept(self):
        with tempfile.TemporaryDirectory() as parent:
            value = config(); evidence = Path(parent)/'ui'; value['evidenceDirectory'] = str(evidence)
            cache = evidence/'datafiles'; cache.mkdir(parents=True)
            self.assertEqual(driver.claim_evidence(value,cache),evidence)

    def test_prior_reports_or_cache_files_are_never_overwritten(self):
        for location in ('report','cache'):
            with tempfile.TemporaryDirectory() as parent:
                value = config(); evidence = Path(parent)/'ui'; value['evidenceDirectory'] = str(evidence)
                cache = evidence/'datafiles'; cache.mkdir(parents=True)
                path = evidence/'old_report.json' if location=='report' else cache/'old_source.blend'
                path.write_bytes(b'prior evidence')
                with self.assertRaises(AssertionError): driver.claim_evidence(value,cache)
                self.assertEqual(path.read_bytes(),b'prior evidence')

    def test_foreign_or_root_datafiles_cache_reject(self):
        value = config()
        for cache in (value['evidenceDirectory'],'D:/shared-datafiles'):
            with self.assertRaises(AssertionError): driver.claim_evidence(value,cache)


if __name__ == '__main__':
    unittest.main()
