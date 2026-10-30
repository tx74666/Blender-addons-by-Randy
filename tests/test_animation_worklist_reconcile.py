"""File-pinned, reversible Unity-list membership contracts without Blender."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class ID(dict):
    __hash__ = object.__hash__

    def __init__(self, **values):
        super().__init__()
        self.__dict__.update(values)

    def as_pointer(self):
        return id(self)


class Rows(list):
    def add(self):
        row = SimpleNamespace()
        self.append(row)
        return row

    def remove(self, index):
        del self[index]


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cd-reconcile-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model = self.root / 'Character.fbx'
        self.manifest = self.root / 'character_animation_workspace.json'
        self.packet = self.root / 'motion.json'
        self.link_path = self.root / 'character_animation_link.json'
        for path in (self.model, self.manifest, self.packet, self.link_path):
            path.write_text(path.name, encoding='utf-8')
        self.rig = ID(name='Artist Rig', type='ARMATURE', library=None, override_library=None,
            data=ID(name='Artist Data', library=None, use_fake_user=False),
            animation_data=SimpleNamespace(action=None))
        self.rig['session'] = {'workspace_identity': 'old', 'mapped_names': ['Hips', 'Spine'],
                               'capture': 'original artist recovery'}
        self.saved = SimpleNamespace(items=Rows(), retired_items=Rows(), catalog=Rows(),
            active_index=-1, catalog_index=-1, workspace_path=str(self.manifest), workspace_identity='old',
            workspace_id='workspace', target_guid='a' * 32, target_name='Cosha',
            model_file=str(self.model), model_sha256=self.sha(self.model),
            prepare_request_id='', prepared_work_item_ids='', connection_check_failed=True,
            composition_workspace_path='', composition_workspace_identity='', rig=self.rig,
            scan_completed=True, status='', has_error=False)
        self.scene = SimpleNamespace(character_designer_animation_worklist=self.saved,
            character_designer_setup=SimpleNamespace(rig=self.rig),
            objects={self.rig.name: self.rig}, unit_settings=SimpleNamespace(scale_length=1.0))
        self.context = SimpleNamespace(scene=self.scene, mode='POSE',
            view_layer=SimpleNamespace(name='ViewLayer', objects={self.rig.name: self.rig}))
        self.record = dict(workspaceId='workspace', targetGuid='a' * 32, targetName='Cosha',
            modelFile=str(self.model), modelSha256=self.sha(self.model),
            _manifest_path=str(self.manifest), clips=[self.clip('walk')])
        self.link = {**self.record['clips'][0], 'targetGuid': 'a' * 32,
                     'identity': 'source-v1', '_manifest_path': str(self.link_path)}
        self.packet_data = {'_path': str(self.packet), '_sha256': self.sha(self.packet)}
        self.identity = lambda record: json.dumps([['schema', record['workspaceId'],
            record['targetGuid'], record['modelFile'], record['modelSha256']], {}])
        self.saved.workspace_identity = self.identity(self.record)
        self.rig['session']['workspace_identity'] = self.saved.workspace_identity
        name = '_reconcile_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        self.bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda value: value))
        self.links = ModuleType(name + '.animation_link')
        self.links._sha256 = self.sha
        self.links.load_link = lambda _path: deepcopy(self.link)
        self.links._identity = lambda record: record['identity']
        self.source = ModuleType(name + '.animation_link_source')
        self.source._source_model = lambda _link, _model: (self.model, self.sha(self.model))
        self.source._packet = lambda _link: (dict(self.packet_data), 'Artist Rig')
        self.workspace = ModuleType(name + '.animation_workspace')
        self.workspace.load_workspace = lambda _path: deepcopy(self.record)
        self.workspace.work_item_id = lambda record: record.get('workItemId', '')
        self.workspace.route_identity = lambda record: (record['workItemId'], record['clipGuid'])
        self.worklist = ModuleType(name + '.animation_worklist')
        self.worklist.state = lambda _context: self.saved
        self.worklist._idle = Mock()
        self.worklist._identity = self.identity
        self.worklist._clip_key = lambda clip: clip['workItemId']
        self.worklist._check_baseline = lambda row: None if row.source_action is not None else self.fail('Missing Source')
        self.worklist._slot = lambda action, slot: slot
        self.worklist.current = lambda _context: self.saved.items[self.saved.active_index] if self.saved.active_index >= 0 else None
        self.worklist._fill_catalog = self.fill_catalog
        self.worklist._capture_rig = self.capture
        self.worklist._restore_rig = self.restore
        self.worklist._finish_rig = Mock()
        self.worklist.adopt_main_rig = Mock(side_effect=self.adopt)
        self.target = ModuleType(name + '.animation_worklist_target')
        self.target.is_adopted = lambda rig: 'session' in rig
        self.target._load = lambda rig: deepcopy(rig['session'])
        self.target._store = lambda rig, record: rig.__setitem__('session', deepcopy(record))
        self.target.ensure_active = Mock()
        self.target.capture_session_metadata = lambda rig: deepcopy(dict(rig))
        self.target.restore_session_metadata = Mock(side_effect=self.restore_metadata)
        self.target.restore_main_rig = Mock(side_effect=lambda _context: self.rig.pop('session', None))
        self.ua = ModuleType(name + '.unity_animation')
        self.ua.active_preview = lambda _rig: False
        self.ua._mapping = Mock(return_value=SimpleNamespace(indices=['Hips', 'Spine'], controls=object()))
        self.composition = ModuleType(name + '.animation_composition')
        self.composition.active = lambda _rig: False
        self.collection = ModuleType(name + '.animation_worklist_collection')
        self.queue = SimpleNamespace(busy=False)
        self.collection.begin_add_ready = Mock(return_value=self.queue)
        self.collection.abort_pending_add = Mock(return_value=True)
        self.fingerprints = ModuleType(name + '.animation_worklist_fingerprint')
        self.fingerprints.fingerprint_action = lambda action, slot: {
            'fingerprint_known': True, 'action_fingerprint': hashlib.sha256(
                json.dumps([dict(action), slot], sort_keys=True).encode()).hexdigest()}
        self.fingerprints.SCHEMA = 'test-fingerprint'
        self.fingerprints.fingerprint_native = Mock()
        modules = {name: package, 'bpy': self.bpy}
        for module in (self.links, self.source, self.workspace, self.worklist,
                       self.target, self.ua, self.composition, self.collection, self.fingerprints):
            modules[module.__name__] = module
        patched = patch.dict(sys.modules, modules)
        patched.start()
        self.addCleanup(patched.stop)
        spec = importlib.util.spec_from_file_location(name + '.animation_worklist_reconcile',
            SOURCE / 'animation_worklist_reconcile.py')
        self.core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.core)
        self.package_name = name

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def clip(self, key):
        return dict(workItemId=key, clipGuid=key, clipName=key.title(),
                    linkManifest=str(self.link_path), needsRebuild=False)

    def row(self, key='walk', *, identity='source-v1'):
        source = ID(name='Source ' + key, library=object(), use_fake_user=False)
        custom = ID(name='Custom ' + key, library=None, use_fake_user=True)
        custom['authored_curve'] = [3, 5, 7]
        row = SimpleNamespace(item_id=uuid.uuid4().hex, work_item_id=key, clip_key=key,
            name=key.title(), source_action=source, custom_action=custom, rig=self.rig,
            custom_slot=1, source_slot=1, new_animation=False, workspace_path='', workspace_identity='',
            manifest_path=str(self.link_path), model_file=str(self.model), model_sha256=self.sha(self.model),
            retirement_reason='', link_identity=json.dumps({**self.link, 'identity': identity}),
            last_synced_receipt='original immutable receipt', source_baseline_receipt='source proof')
        self.saved.items.append(row)
        return row

    def fill_catalog(self, saved, record):
        saved.catalog.clear()
        for clip in record['clips']:
            saved.catalog.append(SimpleNamespace(clip_key=clip['workItemId'], name=clip['clipName']))

    def capture(self, _context, rig):
        return dict(data=rig.data, fake_user=rig.data.use_fake_user,
                    session=deepcopy(dict(rig)), action=rig.animation_data.action)

    def restore(self, _context, rig, captured):
        rig.clear()
        rig.update(captured['session'])
        rig.animation_data.action = captured['action']

    def restore_metadata(self, rig, metadata):
        rig.clear()
        rig.update(deepcopy(metadata))

    def adopt(self, _context):
        self.rig['session'] = dict(workspace_identity=self.saved.workspace_identity,
                                  mapped_names=['Hips', 'Spine'], capture='new recovery')
        self.saved.rig = self.rig
        return self.rig

    def test_plan_is_read_only_and_keeps_exact_existing_custom(self):
        row = self.row()
        before = self.core._digest(self.core._snapshot(self.context, self.saved, self.rig))
        preview = self.core.plan(self.context)
        self.assertEqual(preview['retained_ids'], [row.item_id])
        self.assertEqual(preview['add_keys'], [])
        self.assertEqual(preview['extras'], [])
        self.assertEqual(before, self.core._digest(self.core._snapshot(self.context, self.saved, self.rig)))

    def test_default_snapshot_keeps_full_token_contract_and_rollback_omits_only_contents(self):
        self.row()
        expected_fields = {'workspace', 'active_index', 'rig', 'items', 'retired', 'catalog',
            'scene', 'view_layer', 'rig_data', 'target_session', 'active_action', 'custom_contents'}
        full = self.core._snapshot(self.context, self.saved, self.rig)
        explicit = self.core._snapshot(self.context, self.saved, self.rig, include_custom_contents=True)
        self.assertEqual(set(full), expected_fields)
        encode = lambda value: json.dumps(self.core._stable(value), ensure_ascii=False,
            allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        self.assertEqual(encode(full), encode(explicit))
        with patch.object(self.core, '_custom_content', side_effect=AssertionError('Rollback read Custom proof')):
            rollback = self.core._snapshot(self.context, self.saved, self.rig, include_custom_contents=False)
        self.assertEqual(rollback, {key: value for key, value in full.items() if key != 'custom_contents'})

    def test_preview_and_confirmation_each_fingerprint_custom_once(self):
        row = self.row('extra')
        with patch.object(self.core, 'fingerprint_action', wraps=self.core.fingerprint_action) as proof:
            preview = self.core.plan(self.context)
            self.assertEqual(proof.call_count, 1)
            self.core.begin(self.context, preview['token'], deprecate_extras=True)
        # The old rollback snapshot added a third scan after the fresh review.
        self.assertEqual(proof.call_args_list, [call(row.custom_action, row.custom_slot)] * 2)

    def test_begin_revalidates_custom_once_before_any_membership_change(self):
        row = self.row('extra')
        preview = self.core.plan(self.context)
        original = self.core.fingerprint_action
        def prove_before_retirement(action, slot):
            self.assertIn(row, self.saved.items)
            self.assertEqual(self.saved.retired_items, [])
            return original(action, slot)
        with patch.object(self.core, 'fingerprint_action', side_effect=prove_before_retirement) as proof:
            self.core.begin(self.context, preview['token'], deprecate_extras=True)
        proof.assert_called_once_with(row.custom_action, row.custom_slot)
        self.assertEqual(self.saved.retired_items[0].item_id, row.item_id)

    def test_back_has_no_membership_or_native_action_side_effects(self):
        row = self.row('extra')
        before = self.core._digest(self.core._snapshot(self.context, self.saved, self.rig))
        preview = self.core.plan(self.context)
        with self.assertRaisesRegex(ValueError, 'Retire or Back'):
            self.core.begin(self.context, preview['token'])
        self.assertEqual(before, self.core._digest(self.core._snapshot(self.context, self.saved, self.rig)))
        self.assertIs(self.saved.items[0], row)
        self.collection.begin_add_ready.assert_not_called()

    def test_confirm_retires_complete_metadata_and_native_pointers_without_deletion(self):
        row = self.row('extra')
        authored = row.custom_action
        self.rig.animation_data.action = authored
        preview = self.core.plan(self.context)
        self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.assertEqual(self.saved.items, [])
        archived = self.saved.retired_items[0]
        self.assertIs(archived.custom_action, authored)
        self.assertIs(self.rig.animation_data.action, authored)
        self.assertEqual(authored['authored_curve'], [3, 5, 7])
        self.assertEqual(archived.last_synced_receipt, 'original immutable receipt')
        self.assertEqual(archived.link_identity, row.link_identity)
        self.assertTrue(archived.retirement_reason)
        kwargs = self.collection.begin_add_ready.call_args.kwargs
        self.assertEqual(kwargs['_clip_keys'], ('walk',))
        self.assertIs(kwargs['_main_rig'], self.rig)
        self.assertIn(str(self.model), kwargs['_input_pins'])

    def test_existing_work_is_not_rebaked_or_rebound(self):
        row = self.row()
        action, identity, receipt = row.custom_action, row.link_identity, row.last_synced_receipt
        preview = self.core.plan(self.context)
        self.core.begin(self.context, preview['token'])
        self.assertIs(self.saved.items[0].custom_action, action)
        self.assertEqual(row.link_identity, identity)
        self.assertEqual(row.last_synced_receipt, receipt)
        self.assertEqual(self.collection.begin_add_ready.call_args.kwargs['_clip_keys'], ())

    def test_new_source_with_same_work_id_requires_retirement_then_fresh_addition(self):
        row = self.row(identity='old-source')
        preview = self.core.plan(self.context)
        self.assertEqual(preview['retained_ids'], [])
        self.assertEqual(preview['add_keys'], ['walk'])
        self.assertEqual(preview['extras'][0]['item_id'], row.item_id)

    def test_stale_confirmation_rejects_changed_complete_row_metadata(self):
        row = self.row('extra')
        preview = self.core.plan(self.context)
        row.last_synced_receipt = 'changed after popup'
        with self.assertRaisesRegex(ValueError, 'changed.*Review'):
            self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.assertEqual(self.saved.retired_items, [])

    def test_stale_confirmation_rejects_custom_curve_edit_with_same_action_pointer(self):
        row = self.row('extra')
        preview = self.core.plan(self.context)
        original_action = row.custom_action
        row.custom_action['authored_curve'][1] += 1
        with patch.object(self.core, 'fingerprint_action', wraps=self.core.fingerprint_action) as proof:
            with self.assertRaisesRegex(ValueError, 'local animations changed'):
                self.core.begin(self.context, preview['token'], deprecate_extras=True)
        proof.assert_called_once_with(original_action, row.custom_slot)
        self.assertIs(row.custom_action, original_action)
        self.assertEqual(self.saved.retired_items, [])
        self.collection.begin_add_ready.assert_not_called()

    def test_stale_confirmation_rejects_changed_file_even_same_routing(self):
        self.row('extra')
        preview = self.core.plan(self.context)
        self.link_path.write_text('new unknown metadata', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed.*Review'):
            self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.collection.begin_add_ready.assert_not_called()

    def test_strict_mapping_failure_is_specific_and_prevents_mutation(self):
        self.ua._mapping.side_effect = ValueError('Bind skeletons differ by 2.500 mm: Hips')
        preview = self.core.plan(self.context)
        self.assertIn('2.500 mm', preview['blocked'][0]['reason'])
        with self.assertRaisesRegex(ValueError, 'blocked.*2.500 mm'):
            self.core.begin(self.context, preview['token'])
        self.collection.begin_add_ready.assert_not_called()

    def test_partial_bone_domain_never_reuses_adopted_model(self):
        self.ua._mapping.return_value.indices = ['Hips']
        preview = self.core.plan(self.context)
        self.assertIn('different Main Rig bone inventory', preview['blocked'][0]['reason'])

    def test_physical_model_hash_mismatch_cannot_be_accepted(self):
        self.model.write_text('changed current physical model', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'model hash'):
            self.core.plan(self.context)

    def test_sync_rollback_restores_every_row_pointer_and_context_on_queue_failure(self):
        row = self.row('extra')
        retained = self.row()
        old = self.row('already-retired')
        self.saved.items.pop()
        self.saved.retired_items.append(SimpleNamespace(**vars(old)))
        self.saved.catalog.append(SimpleNamespace(clip_key='saved-catalog', name='Saved catalog'))
        self.saved.catalog_index = 0
        action = row.custom_action
        self.saved.active_index = 0
        self.rig.animation_data.action = action
        original_context = (self.context.mode, self.context.scene, self.context.view_layer.name)
        original_data = self.rig.data
        original_pointers = {entry.item_id: (entry.source_action, entry.custom_action, entry.rig)
            for entry in [*self.saved.items, *self.saved.retired_items]}
        original_action_contents = [(native, deepcopy(dict(native))) for source_action, custom_action, _rig
            in original_pointers.values() for native in (source_action, custom_action)]
        before = self.core._digest(self.core._snapshot(self.context, self.saved, self.rig))
        preview = self.core.plan(self.context)
        def fail_queue(*_args, **_kwargs):
            self.assertEqual([entry.item_id for entry in self.saved.items], [retained.item_id])
            self.assertEqual([entry.item_id for entry in self.saved.retired_items], [old.item_id, row.item_id])
            self.rig.animation_data.action = retained.custom_action
            raise RuntimeError('Timer registration failed')
        self.collection.begin_add_ready.side_effect = fail_queue
        with self.assertRaisesRegex(RuntimeError, 'Timer registration'):
            self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.assertEqual(before, self.core._digest(self.core._snapshot(self.context, self.saved, self.rig)))
        self.assertIs(self.saved.items[0].custom_action, action)
        self.assertIs(self.rig.animation_data.action, action)
        self.assertIs(self.rig.data, original_data)
        self.assertEqual(self.saved.active_index, 0)
        self.assertEqual(self.saved.catalog_index, 0)
        self.assertEqual([entry.item_id for entry in self.saved.items], [row.item_id, retained.item_id])
        self.assertEqual([entry.item_id for entry in self.saved.retired_items], [old.item_id])
        self.assertEqual((self.context.mode, self.context.scene, self.context.view_layer.name), original_context)
        for entry in [*self.saved.items, *self.saved.retired_items]:
            source_action, custom_action, rig = original_pointers[entry.item_id]
            self.assertIs(entry.source_action, source_action)
            self.assertIs(entry.custom_action, custom_action)
            self.assertIs(entry.rig, rig)
        for native, contents in original_action_contents:
            self.assertEqual(dict(native), contents)

    def test_restore_retired_recovers_original_draft_and_provenance(self):
        row = self.row('extra')
        preview = self.core.plan(self.context)
        self.core.begin(self.context, preview['token'], deprecate_extras=True)
        restored = self.core.restore_retired(self.context, row.item_id)
        self.assertIs(restored.custom_action, row.custom_action)
        self.assertEqual(restored.link_identity, row.link_identity)
        self.assertEqual(restored.retirement_reason, '')
        self.assertEqual(self.saved.retired_items, [])

    def retired_with_native_slots(self, *, new_animation=False):
        # Exercise the same slot and baseline validators used by activation,
        # rather than the lightweight plan fixtures' permissive substitutes.
        spec = importlib.util.spec_from_file_location(self.package_name + '.actual_worklist',
            SOURCE / 'animation_worklist.py')
        actual = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(actual)
        self.worklist._slot = Mock(wraps=actual._slot)
        self.worklist._check_baseline = actual._check_baseline
        row = self.row('extra')
        self.saved.items.pop()
        row.retirement_reason = 'Retained artist draft'
        row.source_action.library = SimpleNamespace(filepath=str(self.model))
        row.source_action.slots = [SimpleNamespace(handle=1, target_id_type='OBJECT')]
        row.custom_action.slots = [SimpleNamespace(handle=1, target_id_type='OBJECT')]
        row.source_file, row.source_hash = str(self.model), self.sha(self.model)
        if new_animation:
            row.new_animation, row.source_action = True, None
            row.source_file, row.source_hash = '', ''
            row.link_identity = json.dumps(dict(schema='randomrealm.animation-new-action/1',
                newActionId=uuid.uuid4().hex, workItemId=uuid.uuid4().hex,
                workspaceId=str(uuid.uuid4()), projectId=str(uuid.uuid4()),
                targetGuid='a' * 32, targetHash='b' * 32, baseControllerGuid='c' * 32,
                avatarGuid='d' * 32, avatarHash='e' * 32, modelSha256=self.sha(self.model),
                workspaceTemplateLinkGuid='f' * 32, modelFile=str(self.model),
                workspaceManifest=str(self.manifest), editScope='All'))
        self.saved.retired_items.append(row)
        self.rig.animation_data.action = row.custom_action
        self.rig.animation_data.action_slot = row.custom_action.slots[0]
        return row

    def assert_restore_refused_without_mutation(self, row, reason):
        before = self.core._digest(self.core._snapshot(self.context, self.saved, self.rig))
        row_actions = (row.source_action, row.custom_action)
        actions = [(action, deepcopy(dict(action)), vars(action).copy(),
                    [(slot, vars(slot).copy()) for slot in action.slots])
            for action in (row.source_action, row.custom_action) if action is not None]
        context = (self.context.mode, self.context.scene, self.context.view_layer.name,
                   self.rig.data, self.rig.animation_data.action, self.rig.animation_data.action_slot)
        status = (self.saved.scan_completed, self.saved.status, self.saved.has_error)
        with patch.object(self.core, '_append', wraps=self.core._append) as append:
            with patch.object(self.saved.retired_items, 'remove', wraps=self.saved.retired_items.remove) as remove:
                with self.assertRaisesRegex(ValueError, reason):
                    self.core.restore_retired(self.context, row.item_id)
                append.assert_not_called()
                remove.assert_not_called()
        self.assertEqual(before, self.core._digest(self.core._snapshot(self.context, self.saved, self.rig)))
        self.assertEqual(self.saved.items, [])
        self.assertIs(self.saved.retired_items[0], row)
        self.assertIs(row.source_action, row_actions[0])
        self.assertIs(row.custom_action, row_actions[1])
        self.assertEqual(status, (self.saved.scan_completed, self.saved.status, self.saved.has_error))
        self.assertEqual(context, (self.context.mode, self.context.scene, self.context.view_layer.name,
                                  self.rig.data, self.rig.animation_data.action, self.rig.animation_data.action_slot))
        self.assertIs(self.rig.data, context[3])
        self.assertIs(self.rig.animation_data.action, context[4])
        self.assertIs(self.rig.animation_data.action_slot, context[-1])
        for action, contents, attributes, slots in actions:
            self.assertEqual(dict(action), contents)
            self.assertEqual(vars(action), attributes)
            self.assertEqual(len(action.slots), len(slots))
            for current, (slot, _values) in zip(action.slots, slots):
                self.assertIs(current, slot)
            for slot, values in slots:
                self.assertEqual(vars(slot), values)

    def test_restore_rejects_invalid_saved_action_slots_before_any_membership_write(self):
        for side in ('source_action', 'custom_action'):
            for defect in ('missing', 'wrong_handle', 'non_object', 'ambiguous', 'legacy'):
                with self.subTest(side=side, defect=defect):
                    self.saved.items.clear()
                    self.saved.retired_items.clear()
                    row = self.retired_with_native_slots()
                    action = getattr(row, side)
                    if defect in {'missing', 'legacy'}:
                        action.slots = []
                    elif defect == 'wrong_handle':
                        action.slots[0].handle = 2
                    elif defect == 'non_object':
                        action.slots[0].target_id_type = 'MESH'
                        action.slots.append(SimpleNamespace(handle=2, target_id_type='OBJECT'))
                    else:
                        action.slots.append(SimpleNamespace(handle=1, target_id_type='OBJECT'))
                    if defect == 'legacy':
                        action.is_action_legacy = True
                        action.fcurves = ['Existing legacy artist curve']
                    self.assert_restore_refused_without_mutation(row, 'saved, unambiguous Object slot')

    def test_restore_checks_exact_saved_slots_before_append_and_preserves_other_slots(self):
        row = self.retired_with_native_slots()
        row.source_slot, row.custom_slot = 2, 3
        for action, handle in ((row.source_action, row.source_slot), (row.custom_action, row.custom_slot)):
            action.slots.append(SimpleNamespace(handle=handle, target_id_type='OBJECT'))
        original = self.core._append
        def append_after_both_proofs(rows, values):
            self.assertEqual(self.worklist._slot.call_args_list,
                [call(row.source_action, row.source_slot), call(row.custom_action, row.custom_slot)])
            return original(rows, values)
        with patch.object(self.core, '_append', side_effect=append_after_both_proofs):
            restored = self.core.restore_retired(self.context, row.item_id)
        self.assertIs(restored.source_action, row.source_action)
        self.assertIs(restored.custom_action, row.custom_action)
        self.assertEqual((restored.source_slot, restored.custom_slot), (2, 3))
        self.assertEqual([slot.handle for slot in restored.custom_action.slots], [1, 3])
        self.assertIs(self.rig.animation_data.action, row.custom_action)
        self.assertEqual(self.saved.retired_items, [])

    def test_restore_new_animation_without_source_validates_only_its_custom_slot(self):
        row = self.retired_with_native_slots(new_animation=True)
        restored = self.core.restore_retired(self.context, row.item_id)
        self.worklist._slot.assert_called_once_with(row.custom_action, row.custom_slot)
        self.assertIsNone(restored.source_action)
        self.assertEqual((restored.source_file, restored.source_hash), ('', ''))
        self.assertIs(restored.custom_action, row.custom_action)
        self.assertEqual(restored.link_identity, row.link_identity)
        self.assertEqual(self.saved.retired_items, [])

    def test_restore_new_animation_with_invalid_custom_slot_keeps_retired_draft(self):
        row = self.retired_with_native_slots(new_animation=True)
        row.custom_action.slots[0].handle = 2
        self.assert_restore_refused_without_mutation(row, 'saved, unambiguous Object slot')

    def test_restore_baseline_and_local_custom_guards_remain_before_slot_checks(self):
        for defect, reason in (('source_missing', 'Source Action is missing'),
                ('source_changed', 'read-only Source cache changed'),
                ('custom_missing', 'lost its editable Custom Action'),
                ('custom_linked', 'lost its editable Custom Action')):
            with self.subTest(defect=defect):
                self.saved.items.clear()
                self.saved.retired_items.clear()
                row = self.retired_with_native_slots()
                if defect == 'source_missing':
                    row.source_action = None
                elif defect == 'source_changed':
                    row.source_hash = '0' * 64
                elif defect == 'custom_missing':
                    row.custom_action = None
                else:
                    row.custom_action.library = object()
                self.assert_restore_refused_without_mutation(row, reason)
                self.worklist._slot.assert_not_called()

    def test_restore_never_overwrites_same_work_id_new_source(self):
        old = self.row(identity='old-source')
        preview = self.core.plan(self.context)
        self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.row()
        with self.assertRaisesRegex(ValueError, 'already has this work item'):
            self.core.restore_retired(self.context, old.item_id)
        self.assertEqual(len(self.saved.retired_items), 1)

    def test_other_character_is_rejected_without_retargeting(self):
        self.record['targetGuid'] = 'b' * 32
        with self.assertRaisesRegex(ValueError, 'another character'):
            self.core.plan(self.context)
        self.worklist.adopt_main_rig.assert_not_called()

    def test_missing_adoption_uses_existing_main_rig_and_recovery_not_model_import(self):
        del self.rig['session']
        self.saved.rig = None
        preview = self.core.plan(self.context)
        self.core.begin(self.context, preview['token'])
        self.worklist.adopt_main_rig.assert_called_once_with(self.context)
        self.assertIs(self.saved.rig, self.rig)

    def test_empty_incoming_retires_without_adopting_or_replacing_unadopted_main_rig(self):
        del self.rig['session']
        row = self.row('extra')
        data, custom = self.rig.data, row.custom_action
        self.record['clips'] = []
        preview = self.core.plan(self.context)
        self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.assertEqual(len(self.saved.retired_items), 1)
        self.assertIs(self.saved.retired_items[0].custom_action, custom)
        self.assertIs(self.rig.data, data)
        self.worklist.adopt_main_rig.assert_not_called()
        self.assertNotIn('session', self.rig)

    def test_revision_rebind_queue_failure_explicitly_restores_original_adoption_metadata(self):
        self.row('extra')
        original = deepcopy(self.target.capture_session_metadata(self.rig))
        self.record['workspaceId'] = 'new-workspace'
        preview = self.core.plan(self.context)
        self.collection.begin_add_ready.side_effect = RuntimeError('Queue failed')
        # Deliberately remove the implicit restoration from the older helper;
        # the reconcile transaction must own its explicit metadata rollback.
        self.worklist._restore_rig = Mock()
        with self.assertRaisesRegex(RuntimeError, 'Queue failed'):
            self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.assertEqual(self.target.capture_session_metadata(self.rig), original)
        self.target.restore_session_metadata.assert_called_once_with(self.rig, original)

    def test_finalize_failure_aborts_only_owned_pending_queue_before_state_rollback(self):
        self.row('extra')
        self.queue.busy = True
        before = self.core._digest(self.core._snapshot(self.context, self.saved, self.rig))
        preview = self.core.plan(self.context)
        self.worklist._finish_rig.side_effect = RuntimeError('Finalize failed')
        self.collection.abort_pending_add.side_effect = lambda queue: self.assertEqual(
            self.saved.items, []) or True
        with self.assertRaisesRegex(RuntimeError, 'Finalize failed'):
            self.core.begin(self.context, preview['token'], deprecate_extras=True)
        self.collection.abort_pending_add.assert_called_once_with(self.queue)
        self.assertEqual(before, self.core._digest(self.core._snapshot(self.context, self.saved, self.rig)))

    def real_collection(self):
        spec = importlib.util.spec_from_file_location(self.package_name + '.actual_collection',
            SOURCE / 'animation_worklist_collection.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.bpy.app = SimpleNamespace(timers=SimpleNamespace(
            is_registered=Mock(return_value=True), unregister=Mock()))
        return module

    def test_pending_abort_unregisters_timer_without_launch_and_preserves_foreign_batch(self):
        module = self.real_collection()
        owned = module.SerialQueue(module.ADD, [module.QueueItem('walk')])
        batch = {'queue': owned, 'job': None}
        module._batch = batch
        foreign = module.SerialQueue(module.ADD, [module.QueueItem('other')])
        self.assertFalse(module.abort_pending_add(foreign))
        self.assertIs(module._batch, batch)
        self.bpy.app.timers.unregister.assert_not_called()
        self.assertTrue(module.abort_pending_add(owned))
        self.assertFalse(owned.busy)
        self.assertIsNone(module._batch)
        self.bpy.app.timers.unregister.assert_called_once_with(module._poll_collection)

    def test_pending_abort_refuses_a_started_or_successful_batch(self):
        module = self.real_collection()
        queue = module.SerialQueue(module.ADD, [module.QueueItem('walk')])
        queue.tick(lambda _attempt: None)
        batch = {'queue': queue, 'job': None}
        module._batch = batch
        with self.assertRaisesRegex(ValueError, 'started animation batch'):
            module.abort_pending_add(queue)
        self.assertIs(module._batch, batch)
        self.bpy.app.timers.unregister.assert_not_called()


if __name__ == '__main__':
    unittest.main()
