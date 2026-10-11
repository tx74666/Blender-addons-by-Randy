"""Cheap admission tests for the root-owned native acceptance driver."""
import copy
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
import uuid
from unittest.mock import Mock

PATH = Path(__file__).resolve().parents[1] / 'tools' / 'verify_character_composition_native.py'
SPEC = importlib.util.spec_from_file_location('composition_native_driver', PATH)
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)
IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]


def fixture():
    shifted = IDENTITY[:]
    shifted[3] = 2.
    return dict(schema=driver.SCHEMA, inputBlend='D:/evidence/artist_final_validation.blend',
        inputBlendSha256=driver.SAVED_ARTIST_COPY_SHA256, addonPackage='D:/fixture/character_designer',
        runtimeManifest='D:/fixture/manifest.json', runtimeManifestSha256='a' * 64,
        evidenceDirectory='D:/evidence/native-composition', compositionWorkspace='D:/B/workspace.json',
        compositionWorkspaceSha256='b' * 64,
        historicalModelSha256='1' * 64, compositionModelSha256='2' * 64,
        bWorkItemIds=['22222222-2222-2222-2222-222222222222', '33333333-3333-3333-3333-333333333333'],
        sceneName='Scene', rigName='CoshaRig', expectedNativeCount=217,
        specFiles=[{'path': 'D:/fixture/a1.json', 'sha256': 'c' * 64},
                   {'path': 'D:/fixture/a2.json', 'sha256': 'd' * 64}],
        seekSeconds=[0., .3], objectMotionCases=[IDENTITY[:], shifted],
        preservationHelper={'path': 'D:/fixture/validation_preservation.py', 'sha256': 'e' * 64},
        sessionChecks=None)


class FixtureAdmissionTests(unittest.TestCase):
    def _association_fixture(self):
        action = NS(name='Actual B Custom', content='original imported B')
        native_id = '8' * 32
        identity = {'modelSha256': '2' * 64, 'sourcePackageSha256': 'a' * 64,
                    'linkId': 'actual-fresh-link', 'targetGuid': 'b' * 32,
                    'composition': {'source': 'fresh'}, 'workItemId': '9' * 32}
        rig_values = {'native-rig-id': 'c' * 32, 'native-action': action}
        rig = NS(get=lambda key: rig_values.get(key), animation_data=NS(action=action, action_slot=NS(handle=4)))
        item = NS(item_id=native_id, rig=rig, link_identity=json.dumps(identity), model_sha256='2' * 64,
                  model_file='D:/new/model.fbx', manifest_path='D:/new/link.json',
                  custom_slot=4, custom_action=action, export_rig_name='Actual Rig', clip_key='actual-route',
                  work_item_id='9' * 32, workspace_path='D:/new/workspace.json', workspace_identity='exact workspace',
                  source_file='D:/new/readonly.blend', source_hash='d' * 64)
        links = NS(_identity=lambda value: value, _validate_identity=Mock(),
                   RIG_ID_KEY='native-rig-id', ACTION_KEY='native-action',
                   IDENTITY_FIELDS=tuple(identity), OPTIONAL_IDENTITY_FIELDS=())
        # Exercise the official native-row and association construction bodies,
        # rather than synthesizing an alternative empty-row identity route.
        source = PATH.parents[1] / 'addons/character_designer/animation_worklist.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'_item', '_association_record', '_encoded'}]
        namespace = {'json': json, 'uuid': uuid, 'links': links}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
        saved = NS(rig=rig, target_guid=identity['targetGuid'], items=[item])
        clip = {'key': item.clip_key, 'linkManifest': item.manifest_path}
        catalog = {'modelSha256': '2' * 64, 'clips': [clip]}
        current_link = copy.deepcopy(identity)
        worklist = NS(state=Mock(return_value=saved), _item=namespace['_item'],
                      _fresh_workspace=Mock(return_value=catalog), _clip_key=lambda row: row['key'],
                      _validate_route=Mock(), _check_baseline=Mock(), _association_record=namespace['_association_record'])
        record = json.loads(worklist._association_record(item)[1])
        links.get_link = Mock(return_value=record)
        links.load_link = Mock(return_value=current_link)
        worklist.test_fingerprint = NS(fingerprint_action=lambda action, slot:
            {'fingerprint_known':True, 'fingerprint':action.content, 'slot':slot})
        worklist.test_anchor = driver.capture_import_anchor(object(), rig, native_id, worklist, worklist.test_fingerprint)
        return object(), rig, item, links, worklist, record, catalog

    def _fresh_proof(self, context, rig, item_id, links, worklist):
        return driver.fresh_association_proof(context, rig, item_id, links, worklist, '2' * 64,
            anchor=worklist.test_anchor, fingerprint=worklist.test_fingerprint)

    def test_per_reference_context_copies_require_same_authenticated_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('a1.json', 'a2.json')]
            for path in paths:
                path.write_bytes(b'{"actual":"weapon-context"}')
            ids = fixture()['bWorkItemIds']
            specs = [dict(weaponContextFile=str(path), weaponContextSha256=driver.file_hash(path),
                          editWorkItemId=work_id, referencePacketSha256=letter * 64,
                          editSpeed=1., weaponPrefabGuid='same-actual-prefab')
                     for path, work_id, letter in zip(paths, ids, ('a', 'b'))]
            driver.validate_variant_specs(specs, ids)
            for field, value in (('editSpeed', 2.), ('weaponPrefabGuid', 'other-prefab'),
                                 ('weaponContextSha256', 'f' * 64)):
                wrong = copy.deepcopy(specs)
                wrong[1][field] = value
                with self.assertRaises(AssertionError):
                    driver.validate_variant_specs(wrong, ids)
            paths[1].write_bytes(b'{"actual":"different-context"}')
            with self.assertRaises(AssertionError):
                driver.validate_variant_specs(specs, ids)
            specs[1]['weaponContextSha256'] = driver.file_hash(paths[1])
            with self.assertRaises(AssertionError):
                driver.validate_variant_specs(specs, ids)

    def test_seed_requires_real_completed_physical_and_matching_native_work_ids(self):
        numeric = {'numeric_completed': True, 'same_b_across_two_a_seek_and_object_motion': True,
            'fresh_native_associations_verified': True,
            'fresh_packet_proofs': [{'work_item_id': '1' * 32}, {'work_item_id': '2' * 32}]}
        physical = {'schema': 'x.validation.character-composition-sync/1', 'completed': True,
            'physical_values_equal_foreground_and_two_a': True,
            'items': [{'actualWorkItemId': '1' * 32}, {'actualWorkItemId': '2' * 32}]}
        driver.validate_seed_destination('D:/isolated/qa_seed.blend', numeric, physical)
        for field, replacement in (('completed', False),
                                    ('physical_values_equal_foreground_and_two_a', False)):
            wrong = {**physical, field: replacement}
            with self.assertRaises(AssertionError):
                driver.validate_seed_destination('D:/isolated/qa_seed.blend', numeric, wrong)
        wrong = copy.deepcopy(physical)
        wrong['items'][1]['actualWorkItemId'] = '3' * 32
        with self.assertRaises(AssertionError):
            driver.validate_seed_destination('D:/isolated/qa_seed.blend', numeric, wrong)
        for path in ('qa_seed.blend', 'D:/isolated/qa_seed.json'):
            with self.assertRaises(AssertionError):
                driver.validate_seed_destination(path, numeric, physical)

    def test_actual_fake_user_flags_include_protected_data_and_prior_action_once(self):
        data = NS(use_fake_user=True)
        active_data = NS(use_fake_user=False)
        old_action = NS(use_fake_user=True)
        custom = NS(use_fake_user=True)
        original = {'targets': [{'data': data, 'action': old_action}]}
        active = {'targets': [{'data': active_data, 'action': custom}]}
        flags = driver.capture_fake_users(original, active, [NS(source_action=old_action, custom_action=custom)])
        self.assertEqual(len(flags), 4)
        self.assertEqual([flag for _owner, flag in flags], [True, True, False, True])

    def test_exact_saved_copy_and_meaningful_two_a_fixture_accepts(self):
        value = fixture()
        self.assertIs(driver.validate_fixture(value), value)

    def test_live_artist_and_wrong_saved_hash_reject(self):
        for field, value in [('inputBlend', 'D:/Blender/Projects/Character/X/X.blend'),
                             ('inputBlendSha256', 'f' * 64)]:
            data = fixture()
            data[field] = value
            with self.assertRaises(AssertionError):
                driver.validate_fixture(data)

    def test_missing_field_and_invented_target_reject(self):
        for field, value in [('sceneName', 'Another Scene'), ('rigName', 'CoshaRig.001'),
                             ('expectedNativeCount', 346)]:
            data = fixture()
            data[field] = value
            with self.assertRaises(AssertionError):
                driver.validate_fixture(data)
        data = fixture()
        del data['runtimeManifestSha256']
        with self.assertRaises(AssertionError):
            driver.validate_fixture(data)

    def test_identical_a_or_seek_input_reject(self):
        for key in ('specFiles', 'seekSeconds', 'objectMotionCases'):
            data = fixture()
            data[key][1] = copy.deepcopy(data[key][0])
            with self.assertRaises(AssertionError):
                driver.validate_fixture(data)

    def test_nonfinite_time_and_size_altering_object_motion_reject(self):
        for key, value in [('seekSeconds', [0., float('nan')]), ('seekSeconds', [-1., .2])]:
            data = fixture()
            data[key] = value
            with self.assertRaises(AssertionError):
                driver.validate_fixture(data)
        for diagonal in (2., -1.):
            data = fixture()
            data['objectMotionCases'][1][0] = diagonal
            with self.assertRaises(AssertionError):
                driver.validate_fixture(data)

    def test_root_motion_can_rotate_and_translate_without_scale_exception(self):
        data = fixture()
        data['objectMotionCases'][1] = [0., -1., 0., 2., 1., 0., 0., 0.,
                                      0., 0., 1., 0., 0., 0., 0., 1.]
        driver.validate_fixture(data)

    def test_unlisted_runtime_module_rejected_before_addon_import(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            package = folder / 'character_designer'
            package.mkdir()
            init = package / '__init__.py'
            init.write_text("bl_info = {'version': (0, 77, 25)}", encoding='utf-8')
            (package / 'unlisted.py').write_text('unapproved = True', encoding='utf-8')
            manifest = folder / 'projection.json'
            manifest.write_text(json.dumps({'schema_version': 1, 'module': 'character_designer',
                'source_root': str(package), 'version': '0.77.25', 'source_provenance': {'fixture': True},
                'files': {'__init__.py': driver.file_hash(init)}}), encoding='utf-8')
            config = fixture()
            config.update(addonPackage=str(package), runtimeManifest=str(manifest),
                          runtimeManifestSha256=driver.file_hash(manifest))
            with self.assertRaisesRegex(ValueError, 'extra=.*unlisted.py'):
                driver.runtime_projection(config)

    def test_historical_model_cannot_substitute_for_actual_fresh_model(self):
        for value in ('1' * 64, 'not-a-sha256'):
            data = fixture()
            data['compositionModelSha256'] = value
            with self.assertRaises(AssertionError):
                driver.validate_fixture(data)

    def test_fresh_association_requires_native_action_slot_and_current_link(self):
        context, rig, item, links, worklist, record, _catalog = self._association_fixture()
        proof = self._fresh_proof(context, rig, item.item_id, links, worklist)
        self.assertEqual(proof['model_sha256'], '2' * 64)
        worklist._fresh_workspace.assert_called_once_with(worklist.state(context), item)
        worklist._validate_route.assert_called_once()
        worklist._check_baseline.assert_called_once_with(item)
        for target, field, changed in ((rig.animation_data, 'action', object()),
                (rig.animation_data.action_slot, 'handle', 9), (item, 'model_sha256', '1' * 64)):
            previous = getattr(target, field)
            setattr(target, field, changed)
            with self.assertRaises(AssertionError):
                self._fresh_proof(context, rig, item.item_id, links, worklist)
            setattr(target, field, previous)
        record['identity'] = {**record['identity'], 'composition': {'source': 'historical'}}
        with self.assertRaises(AssertionError):
            self._fresh_proof(context, rig, item.item_id, links, worklist)

    def test_fresh_association_reacquires_id_after_old_collection_alias_is_invalid(self):
        context, rig, item, links, worklist, _record, _catalog = self._association_fixture()
        native_id = item.item_id
        old_alias = NS(item_id=native_id, link_identity='')
        proof = self._fresh_proof(context, rig, old_alias.item_id, links, worklist)
        self.assertEqual(proof['packet_sha256'], 'a' * 64)
        # Empty identity in the actual current row is still a hard failure.
        item.link_identity = ''
        with self.assertRaises((AssertionError, json.JSONDecodeError)):
            self._fresh_proof(context, rig, native_id, links, worklist)

    def test_coherent_same_id_action_and_content_rebind_cannot_replace_import_anchor(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                context, rig, item, links, worklist, _record, _catalog = self._association_fixture()
                if replacement:
                    item.custom_action = NS(name=item.custom_action.name, content='changed B content')
                else:
                    item.custom_action.content = 'changed B content'
                # Same native row/spec/model plus a coherently rebound rig and
                # saved association must not become a new imported baseline.
                rig.animation_data.action = item.custom_action
                rig.get = lambda key: item.custom_action if key == links.ACTION_KEY else 'c' * 32
                links.get_link.return_value = json.loads(worklist._association_record(item)[1])
                with self.assertRaisesRegex(AssertionError, 'original per-Add anchor'):
                    self._fresh_proof(context, rig, item.item_id, links, worklist)

    def test_fresh_association_rejects_missing_foreign_or_changed_prepared_route(self):
        for damage in ('missing', 'duplicate', 'foreign-rig', 'model', 'rebuilt', 'readonly', 'manifest', 'workspace', 'link'):
            with self.subTest(damage=damage):
                context, rig, item, links, worklist, _record, catalog = self._association_fixture()
                saved = worklist.state(context)
                if damage == 'missing': saved.items.clear()
                elif damage == 'duplicate': saved.items.append(item)
                elif damage == 'foreign-rig': item.rig = object()
                elif damage == 'model': catalog['modelSha256'] = '1' * 64
                elif damage == 'rebuilt': catalog['clips'][0]['needsRebuild'] = True
                elif damage == 'readonly': catalog['clips'][0]['compositionReferenceSource'] = True
                elif damage == 'manifest': catalog['clips'][0]['linkManifest'] = 'D:/foreign/link.json'
                elif damage == 'workspace': worklist._fresh_workspace.side_effect = ValueError('workspace changed')
                else: worklist._validate_route.side_effect = ValueError('actual prepared Link changed')
                with self.assertRaises((AssertionError, ValueError)):
                    self._fresh_proof(context, rig, item.item_id, links, worklist)

    def test_intentional_ui_reopen_cannot_share_native_recovery_scope(self):
        data = fixture()
        data['sessionChecks'] = {'evidenceDirectory': 'D:/ui-test'}
        with self.assertRaisesRegex(AssertionError, 'separate fixture process'):
            driver.validate_fixture(data)

    def test_copied_scene_clears_only_its_rows_and_keeps_native_action_ids(self):
        action = object()
        original = NS()
        original_saved = NS(items=[action], catalog=['old'], composition_catalog=['old'])
        original.character_designer_animation_worklist = original_saved
        copied = NS()
        copied_saved = NS(items=list(original_saved.items), catalog=['old'], composition_catalog=['old'], id_data=copied)
        copied.character_designer_animation_worklist = copied_saved
        driver.clear_copied_worklist(copied, original, None)
        self.assertEqual(original_saved.items, [action])
        self.assertIs(original_saved.items[0], action)
        self.assertEqual(copied_saved.items, [])
        self.assertEqual(copied_saved.workspace_identity, '')

    def test_owned_scene_seam_rejects_original_or_foreign_worklist_state(self):
        scene = NS()
        saved = NS(items=['protected'], id_data=scene)
        scene.character_designer_animation_worklist = saved
        with self.assertRaises(AssertionError):
            driver.clear_copied_worklist(scene, scene, None)
        another = NS(character_designer_animation_worklist=saved)
        with self.assertRaises(AssertionError):
            driver.clear_copied_worklist(another, scene, None)
        self.assertEqual(saved.items, ['protected'])


if __name__ == '__main__':
    unittest.main()
