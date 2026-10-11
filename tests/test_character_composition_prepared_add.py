"""Prepared Add routing executes canonical code; native import boundaries mocked."""
from copy import deepcopy
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE = Path(__file__).resolve().parent.parent / 'addons' / 'character_designer'


class Rows(list):
    def add(self):
        row = NS()
        self.append(row)
        return row


class PreparedAddTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cd-prepared-add-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = '_cd_prepared_add_' + uuid.uuid4().hex
        package = ModuleType(self.package)
        package.__path__ = [str(SOURCE)]
        self.bpy = ModuleType('bpy')
        self.timers = NS(register=Mock(), unregister=Mock(), is_registered=Mock(return_value=True))
        self.bpy.app, self.bpy.data = NS(timers=self.timers), NS(user_map=Mock())
        modules = patch.dict(sys.modules, {self.package: package, 'bpy': self.bpy})
        modules.start(); self.addCleanup(modules.stop)
        self.links = self.service('animation_link')
        self.source = self.service('animation_link_source')
        self.ua = self.service('unity_animation')
        self.target = self.service('animation_worklist_target')
        self.target.managed, self.target.is_adopted = Mock(return_value=True), Mock(return_value=True)
        self.target.ensure_active = Mock()
        self.animation = self.service('animation')
        self.animation._link_idle = Mock(side_effect=self.idle)
        fingerprints = self.service('animation_worklist_fingerprint')
        fingerprints.SCHEMA, fingerprints.fingerprint_native = 'test-native-proof/1', Mock()
        for short in ('animation_composition_contract', 'animation_workspace', 'animation_worklist_queue',
                      'animation_worklist', 'animation_worklist_collection'):
            self.load(short)
        self.contract, self.workspace = package.animation_composition_contract, package.animation_workspace
        self.worklist, self.collection = package.animation_worklist, package.animation_worklist_collection
        self.composition = self.service('animation_composition')
        self.composition.active = Mock(return_value=False)
        self.composition.add_work_item = Mock(side_effect=self.import_b)
        self.library = self.root / self.workspace.MANIFEST_NAME
        self.manifest = self.root / 'character_animation_link.json'
        self.manifest.write_text('{}', encoding='utf-8')
        self.spec = self.contract.validate(dict(schema=self.contract.SCHEMA, compositionId='2'*32,
            applicationRole=self.contract.ROLE, baseClipGuid='3'*32, baseClipLocalId=42,
            baseClipSha256='4'*64, referencePacketFile=str(self.root/'A.cdanim.json'),
            referencePacketSha256='5'*64, editWorkItemId='1'*32, editNewActionId='',
            mask='UpperBody', blendMode='Override', weight=1, humanRoles=['Chest'],
            allowedBonePaths=['Rig/Hips/Spine/Chest'], editableControlIds=['chest'], sourceRestSha256='6'*64,
            timeMode='HoldLoop', referenceSpeed=1, editSpeed=1, referencePhase=0, editPhase=0,
            fps=30, sampleStart=0, sampleEnd=1, holdWorkItemId='', holdNewActionId='',
            weaponGuid='', weaponPrefabGuid='', weaponModelFile='', weaponModelSha256='',
            weaponSocketPath='', weaponRigBonePath='', weaponLocalMatrix=None,
            weaponPrefabLocalMatrix=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.],
            weaponContextFile=str(self.root/'weapon.json'), weaponContextSha256='7'*64, gripTargets=[]))
        self.link = dict(targetGuid='a'*32, clipGuid='b'*32, clipLocalId=12, workItemId='1'*32,
            composition=deepcopy(self.spec), editScope=self.contract.SCOPE)
        self.clip = dict(deepcopy(self.link), clipName='Actual B', linkManifest=str(self.manifest), needsRebuild=False)
        self.catalog = dict(schema=self.workspace.SCHEMA, workspaceId='8'*32, targetGuid='a'*32,
            targetName='Cosha', modelFile=str(self.root/'Model.fbx'), modelSha256='9'*64,
            _manifest_path=str(self.library), clips=[self.clip])
        self.catalog.update(prepareRequestId='c'*32, preparedWorkItemIds=['1'*32])
        self.artist_action = object()
        self.rig = NS(animation_data=NS(action=self.artist_action))
        self.saved = NS(rig=self.rig, items=Rows(), active_index=-1, target_guid='a'*32,
            composition_workspace_path=str(self.library), catalog=Rows(), catalog_index=-1,
            prepare_request_id='', prepared_work_item_ids='', status='', has_error=False,
            collection_status='', scan_completed=False)
        self.scene = NS(name='Original Scene', character_designer_animation_worklist=self.saved)
        self.window = NS(scene=self.scene, screen=NS(areas=[]))
        self.context = NS(window=self.window, scene=self.scene, object=None,
            window_manager=NS(windows=[self.window]), temp_override=lambda **_: nullcontext())
        self.bpy.context = self.context
        self.worklist._fresh_workspace = Mock(side_effect=lambda *_: deepcopy(self.catalog))
        self.worklist._prepared = Mock(side_effect=AssertionError('Legacy native importer reached'))
        self.worklist.activate = Mock()
        self.links.load_link = Mock(side_effect=lambda _: deepcopy(self.link))
        self.composition._library_state = Mock(side_effect=lambda _: (deepcopy(self.catalog),
            NS(workspace_identity=self.worklist._identity(self.catalog))))
        self.addCleanup(self.cleanup_queue)

    def load(self, short):
        name = self.package + '.' + short
        spec = importlib.util.spec_from_file_location(name, SOURCE/(short+'.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module; self.addCleanup(sys.modules.pop, name, None)
        spec.loader.exec_module(module)
        setattr(sys.modules[self.package], short, module)
        return module

    def service(self, short):
        module = ModuleType(self.package+'.'+short)
        sys.modules[module.__name__] = module; self.addCleanup(sys.modules.pop, module.__name__, None)
        setattr(sys.modules[self.package], short, module)
        return module

    def idle(self, *, include_collection=True):
        return not include_collection or not self.collection.running()

    def cleanup_queue(self):
        self.collection._batch = None

    def import_b(self, context, key, *, activate_new, _collection):
        self.worklist._idle(context, _collection=_collection)
        item = NS(clip_key=key, item_id='native B row')
        self.saved.items.append(item)
        if activate_new:
            self.worklist.activate(context, item.item_id, 'CUSTOM', _collection=_collection)
        return item

    def key(self):
        return self.workspace.row_key(self.clip)

    def use_actual_composition_importer(self):
        self.composition = self.load('animation_composition')
        controls = self.load('animation_composition_controls')
        package = sys.modules[self.package]
        self.composition._host = lambda name: getattr(package, name)
        self.composition.active = Mock(return_value=False)
        self.scope_service = NS(resolve_scope=Mock())
        self.composition._service = lambda name: controls if name == 'animation_composition_controls' else self.scope_service
        self.library_state = NS(workspace_path=str(self.library), workspace_identity=self.worklist._identity(self.catalog),
            model_file=self.catalog['modelFile'], model_sha256=self.catalog['modelSha256'])
        self.composition._library_state = Mock(side_effect=lambda _: (deepcopy(self.catalog), self.library_state))
        self.packet = dict(_sha256='0'*64, _path=str(self.root/'B.cdanim.json'), composition=deepcopy(self.spec))
        self.links.PACKAGE_KEY, self.links.PACKAGE_HASH_KEY = 'packet', 'packet_hash'
        self.links._identity = self.workspace.route_identity
        self.ua.load_package = Mock(side_effect=lambda _: deepcopy(self.packet))
        self.source._source_model, self.source._import_action = Mock(), Mock()
        self.worklist._check_baseline = Mock()
        self.worklist._prepared.side_effect = lambda *_args, **_kw: (deepcopy(self.catalog), deepcopy(self.clip),
            deepcopy(self.link), deepcopy(self.packet), 'CoshaRig', self.root/'Model.fbx')

    def existing_b(self):
        row = NS(clip_key=self.key(), item_id='existing B', work_item_id=self.spec['editWorkItemId'],
            composition_json=json.dumps(self.spec), composition_sha256=self.contract.identity(self.spec),
            application_role=self.contract.ROLE, edit_scope=self.contract.SCOPE,
            link_identity=json.dumps(self.link), workspace_path=str(self.library),
            workspace_identity=self.library_state.workspace_identity, model_sha256=self.library_state.model_sha256,
            custom_action={'packet':self.packet['_path'], 'packet_hash':self.packet['_sha256']})
        self.saved.items.append(row)
        return row

    def reject_without_native_mutation(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            self.worklist.add(self.context, self.key(), activate_new=False, _collection=True)
        self.composition.add_work_item.assert_not_called()
        self.target.ensure_active.assert_not_called()
        self.worklist._prepared.assert_not_called()
        self.worklist.activate.assert_not_called()
        self.assertEqual(self.saved.items, [])
        self.assertIs(self.rig.animation_data.action, self.artist_action)

    def test_generic_prepared_b_dispatches_exact_route_and_flags(self):
        row = self.worklist.add(self.context, self.key(), activate_new=False, _collection=True)
        self.composition.add_work_item.assert_called_once_with(self.context, self.key(), activate_new=False, _collection=True)
        self.assertIs(row, self.saved.items[0])
        self.worklist._prepared.assert_not_called(); self.target.ensure_active.assert_not_called()
        self.worklist.activate.assert_not_called()
        self.assertIs(self.rig.animation_data.action, self.artist_action)

    def test_manual_prepared_b_requests_authenticated_custom_activation(self):
        row = self.worklist.add(self.context, self.key())
        self.composition.add_work_item.assert_called_once_with(self.context, self.key(), activate_new=True, _collection=False)
        self.worklist.activate.assert_called_once_with(self.context, row.item_id, 'CUSTOM', _collection=False)

    def test_serial_add_prepared_exact_ids_uses_composition_under_owned_lock(self):
        extra = dict(self.clip, workItemId='d'*32, composition=dict(self.spec, editWorkItemId='d'*32))
        reference = dict(self.clip, composition=None, editScope='All', workItemId='e'*32,
            compositionReferenceSource=True, compositionOwnerLinkGuid='f'*32)
        self.catalog['clips'].extend([extra, reference])
        self.catalog['preparedWorkItemIds'].append('e'*32)
        queue = self.collection.begin_add_ready(self.context)
        self.assertEqual(queue.remaining_keys, (self.key(),))
        self.collection._poll_collection()
        self.assertEqual(queue.succeeded_keys, (self.key(),))
        self.composition.add_work_item.assert_called_once_with(self.context, self.key(), activate_new=False, _collection=True)
        self.assertIs(self.context.window.scene, self.scene)
        self.assertIs(self.rig.animation_data.action, self.artist_action)
        self.worklist.activate.assert_not_called()

    def test_missing_library_rejects_before_any_import_or_binding(self):
        self.saved.composition_workspace_path = ''
        self.reject_without_native_mutation('Connect Composition Library to this workspace')

    def test_other_library_path_is_retained_and_rejects_before_mutation(self):
        other = dict(self.catalog, _manifest_path=str(self.root/'Different'/self.workspace.MANIFEST_NAME))
        self.composition._library_state.side_effect = lambda _: (other, NS(workspace_identity=self.worklist._identity(self.catalog)))
        self.reject_without_native_mutation('existing library was retained')

    def test_other_library_identity_rejects_before_mutation(self):
        self.composition._library_state.side_effect = lambda _: (self.catalog, NS(workspace_identity='other model'))
        self.reject_without_native_mutation('existing library was retained')

    def test_readonly_a_with_real_controller_slot_rejects_generic_add(self):
        self.clip.update(composition=None, editScope='All', compositionReferenceSource=True, compositionOwnerLinkGuid='f'*32)
        self.reject_without_native_mutation('read-only A reference')

    def test_sourceonly_a_rejects_generic_add_before_loading_link(self):
        self.clip.update(composition=None, editScope='All', sourceOnly=True, baseClipGuid='', baseClipLocalId=0)
        self.reject_without_native_mutation('read-only A reference')
        self.links.load_link.assert_not_called()

    def test_missing_b_dto_cannot_fall_back_to_ordinary_import(self):
        self.clip.pop('composition'); self.link.pop('composition')
        self.reject_without_native_mutation('UpperBodyOverride requires')

    def test_swapped_link_full_spec_rejects_before_b_or_legacy_import(self):
        self.link['composition']['referencePacketSha256'] = '0'*64
        self.reject_without_native_mutation('another work item')

    def test_existing_ordinary_and_finger_rows_keep_legacy_activation(self):
        for scope in ('All', 'FingersLeft', 'FingersRight', 'FingersBoth'):
            with self.subTest(scope=scope):
                row = NS(clip_key=self.key(), item_id='old', edit_scope=scope,
                    link_identity=json.dumps(dict(self.link, composition=None, editScope=scope)))
                self.saved.items[:] = [row]
                self.assertIs(self.worklist.add(self.context, self.key()), row)
        self.assertEqual(self.worklist.activate.call_count, 4)
        self.worklist._fresh_workspace.assert_not_called(); self.composition.add_work_item.assert_not_called()

    def test_existing_b_missing_local_dto_routes_to_strict_importer_and_retains_draft(self):
        row = NS(clip_key=self.key(), item_id='old B', link_identity=json.dumps(self.link), composition_json='')
        self.saved.items.append(row)
        self.composition.add_work_item.side_effect = ValueError('no authenticated local DTO')
        with self.assertRaisesRegex(ValueError, 'no authenticated local DTO'):
            self.worklist.add(self.context, self.key())
        self.worklist.activate.assert_not_called(); self.worklist._prepared.assert_not_called()
        self.assertEqual(self.saved.items, [row])

    def test_existing_b_actual_importer_full_seal_preflight_before_activation(self):
        self.use_actual_composition_importer()
        row = self.existing_b()
        self.assertIs(self.worklist.add(self.context, self.key(), _collection=True), row)
        self.scope_service.resolve_scope.assert_called_once()
        self.target.ensure_active.assert_called_once_with(self.context, self.rig)
        self.worklist.activate.assert_called_once_with(self.context, row.item_id, 'CUSTOM', _collection=True)
        self.source._import_action.assert_not_called()

    def test_existing_b_actual_importer_false_flag_preserves_selection_and_binding(self):
        self.use_actual_composition_importer()
        row = self.existing_b()
        self.saved.active_index = 12
        self.assertIs(self.worklist.add(self.context, self.key(), activate_new=False, _collection=True), row)
        self.assertEqual(self.saved.active_index, 12)
        self.worklist.activate.assert_not_called()
        self.assertIs(self.rig.animation_data.action, self.artist_action)

    def test_existing_b_actual_importer_missing_dto_rejects_before_native_mutation(self):
        self.use_actual_composition_importer()
        row = self.existing_b(); row.composition_json = ''
        with self.assertRaisesRegex(ValueError, 'no authenticated local DTO'):
            self.worklist.add(self.context, self.key())
        self.target.ensure_active.assert_not_called(); self.worklist.activate.assert_not_called()
        self.source._import_action.assert_not_called()
        self.assertEqual(self.saved.items, [row])

    def test_batch_actual_importer_rejects_unsealed_b_before_native_mutation(self):
        self.use_actual_composition_importer()
        self.packet['composition']['referencePacketSha256'] = 'e'*64
        queue = self.collection.begin_add_ready(self.context)
        self.collection._poll_collection()
        self.assertEqual(queue.succeeded_keys, ())
        self.target.ensure_active.assert_not_called(); self.worklist.activate.assert_not_called()
        self.source._import_action.assert_not_called()
        self.assertEqual(self.saved.items, [])
        self.assertIn('differs from its sealed source packet', self.saved.collection_status)

    def test_batch_actual_importer_commits_b_dto_and_reference_before_leaving_artist_binding(self):
        self.use_actual_composition_importer()
        self.library_state.target_name = 'Cosha'
        self.link['_manifest_path'] = str(self.manifest)
        baseline, custom, linked = NS(), NS(), NS()
        baseline.copy = Mock(return_value=custom)
        self.source._import_action.return_value = NS(action=baseline)
        self.source._context_state, self.source._restore_context = Mock(), Mock()
        original_data = NS(use_fake_user=False)
        self.worklist._capture_rig = Mock(return_value={'data':original_data, 'fake_user':False})
        self.worklist._restore_rig, self.worklist._clean_action = Mock(), Mock()
        self.worklist._linked_baseline = Mock(return_value=(linked, str(self.root/'readonly-source.blend'), 'f'*64))
        self.worklist._slot = Mock(return_value=NS(handle=7))
        self.target.restore_import_preview, self.target.attach_action_motion_data = Mock(), Mock()
        self.composition.prepare_reference = Mock()
        self.collection.remember_source_baseline = Mock()
        self.bpy.data.actions = NS(remove=Mock())
        self.saved.active_index = 12
        queue = self.collection.begin_add_ready(self.context)
        self.collection._poll_collection()
        self.assertEqual(queue.succeeded_keys, (self.key(),), self.saved.collection_status)
        row = self.saved.items[0]
        self.assertEqual(self.composition.item_spec(row), self.spec)
        self.assertEqual(row.work_item_id, uuid.UUID(self.spec['editWorkItemId']).hex)
        self.assertEqual(row.edit_scope, self.contract.SCOPE)
        self.assertIs(row.source_action, linked); self.assertIs(row.custom_action, custom)
        self.composition.prepare_reference.assert_called_once_with(self.context, row, self.spec, _collection=True)
        self.collection.remember_source_baseline.assert_called_once_with(self.context, row)
        self.worklist.activate.assert_not_called()
        self.assertEqual(self.saved.active_index, 12)
        self.assertIs(self.rig.animation_data.action, self.artist_action)
        self.assertIs(self.context.window.scene, self.scene)

    def test_catalog_and_addready_omit_readonly_companions_but_keep_b(self):
        for flag in ('sourceOnly', 'compositionReferenceSource'):
            reference = dict(self.clip, composition=None, editScope='All', workItemId='e'*32)
            reference[flag] = True
            if flag == 'sourceOnly': reference.update(baseClipGuid='', baseClipLocalId=0)
            else: reference['compositionOwnerLinkGuid'] = 'f'*32
            self.catalog['clips'] = [self.clip, reference]
            self.worklist._fill_catalog(self.saved, self.catalog)
            self.assertEqual([row.clip_key for row in self.saved.catalog], [self.key()])

    def test_row_becoming_readonly_after_queue_stops_before_dispatch(self):
        queue = self.collection.begin_add_ready(self.context)
        self.clip.update(composition=None, editScope='All', compositionReferenceSource=True, compositionOwnerLinkGuid='f'*32)
        self.collection._poll_collection()
        self.assertEqual(queue.succeeded_keys, ())
        self.assertIn('read-only A reference', self.saved.collection_status)
        self.composition.add_work_item.assert_not_called(); self.worklist._prepared.assert_not_called()


if __name__ == '__main__':
    unittest.main()
