"""Actual Worklist routing/transactions with fake native boundary APIs.

Real workspace/Link JSON validators and actual Add/Activate bodies execute.
The packet importer, adopted Rest/control mapper and twenty-channel carrier
validator are explicit fakes: these cases do not certify Blender or a solver.
"""
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
CAPTURE = 'character_designer_rigging_capture_v1'
INPUT = 'character_designer_rigging_input_action_v1'


class ID(dict):
    __hash__ = object.__hash__
    __eq__ = object.__eq__

    def __bool__(self):
        return True

    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)
        self.is_embedded_data = False


class Collection(list):
    def add(self):
        item = Row(workspace_path='', workspace_identity='', side='CUSTOM', _native_ptr=uuid.uuid4().int,
                   source_action=None, source_file='', source_hash='', new_animation=False)
        self.append(item)
        return item

    def remove(self, index):
        del self[index]


class Row(SimpleNamespace):
    def as_pointer(self):
        return self._native_ptr


class AttachTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cd-attach-pure-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.events, self.ids, self.removed = [], set(), set()
        self.package_name = '_attach_source_' + uuid.uuid4().hex
        package = ModuleType(self.package_name)
        package.__path__ = [str(SOURCE)]
        ua = ModuleType(self.package_name + '.unity_animation')
        source = ModuleType(self.package_name + '.animation_link_source')
        target = ModuleType(self.package_name + '.animation_worklist_target')
        composition = ModuleType(self.package_name + '.animation_composition')
        composition.active = lambda _rig: False
        composition.item_spec = lambda _item: None
        rigging_contract = ModuleType(self.package_name + '.animation_rigging_contract')
        rigging_contract.validate_capture = lambda value, *, packet: deepcopy(value)
        collection = ModuleType(self.package_name + '.animation_worklist_collection')
        collection.remember_source_baseline = lambda _ctx, item: setattr(item, 'fingerprint_known', False)
        self.bpy = SimpleNamespace(path=SimpleNamespace(abspath=lambda value: value),
            data=SimpleNamespace(user_map=lambda: {value: () for value in self.ids},
                batch_remove=self.remove_ids, actions=SimpleNamespace(remove=lambda value: self.ids.remove(value))))
        modules = {self.package_name: package, 'bpy': self.bpy}
        for name, value in (('unity_animation', ua), ('animation_link_source', source),
                ('animation_worklist_target', target), ('animation_composition', composition),
                ('animation_rigging_contract', rigging_contract), ('animation_worklist_collection', collection)):
            modules[self.package_name + '.' + name] = value
            setattr(package, name, value)
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start(); self.addCleanup(self.module_patch.stop)

        def load(name):
            spec = importlib.util.spec_from_file_location(self.package_name + '.' + name, SOURCE / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            self.addCleanup(sys.modules.pop, spec.name, None)
            spec.loader.exec_module(module)
            setattr(package, name, module)
            return module

        self.links, self.workspace, self.worklist = (load(name) for name in
            ('animation_link', 'animation_workspace', 'animation_worklist'))
        self.new = load('animation_worklist_new')
        self.target, self.source, self.ua = target, source, ua
        self.model = self.folder / 'OriginalModel.fbx'; self.model.write_bytes(b'one exact physical model')
        self.model_sha = self.links._sha256(self.model)
        self.original_action = self.action('Original old Custom', 17)
        self.original_data = ID(name='Original motion data', use_fake_user=False)
        self.rig = ID(name='Original CoshaRig', type='ARMATURE', library=None, data=self.original_data,
                      animation_data=SimpleNamespace(action=self.original_action), show_in_front=True, mode='OBJECT')
        self.rig.animation_data_create = lambda: self.rig.animation_data
        self.rig.hide_set = self.rig.select_set = lambda _value: None
        self.rig['artist_design'] = ['original modifiers/colors/controllers']
        self.rig['character_designer_animation_source_model_sha256'] = self.model_sha
        self.ids.update((self.rig, self.original_data))
        self.saved = SimpleNamespace(items=Collection(), catalog=Collection(), active_index=0,
            workspace_path=str(self.folder / 'old_workspace.json'), workspace_identity='original sealed header',
            workspace_id=str(uuid.uuid4()), target_guid='a' * 32, target_name='Original character',
            model_file=str(self.model), model_sha256=self.model_sha, rig=self.rig, status='original', has_error=False)
        for index in range(9):
            self.saved.items.append(Row(item_id='old-' + str(index), clip_key='oldkey-' + str(index),
                rig=self.rig, custom_action=self.original_action, source_action=None, side='CUSTOM',
                workspace_path='', workspace_identity='', work_item_id='', _native_ptr=uuid.uuid4().int))
        self.scene = SimpleNamespace(name='Original Scene', character_designer_animation_worklist=self.saved,
            character_designer_setup=SimpleNamespace(rig=self.rig), unit_settings=SimpleNamespace(scale_length=.01),
            tool_settings=SimpleNamespace(use_keyframe_insert_auto=False),
            objects={self.rig.name: self.rig})
        self.context = SimpleNamespace(scene=self.scene, object=self.rig, window=None,
            window_manager=SimpleNamespace(character_designer_animation=SimpleNamespace(target=self.rig)),
            view_layer=SimpleNamespace(name='Original ViewLayer', objects=SimpleNamespace(active=self.rig),
                                       update=lambda: None), selected_objects=[])
        self.mapping_names = {'Hips', 'RightHand', 'LeftHand'}
        target.is_adopted = lambda rig: rig is self.rig
        target.managed = lambda rig: rig is self.rig
        target.ensure_active = lambda _ctx, rig: self.events.append('ensure original')
        target._load = lambda _rig: {'mapped_names': sorted(self.mapping_names)}
        ua._mapping = lambda _rig, _packet, _scale: SimpleNamespace(indices={name: i for i, name in
            enumerate(self.mapping_names)}, controls=SimpleNamespace())
        ua.active_preview = lambda _rig: False
        ua._set_playing = lambda *_args: None
        ua._set_frame = lambda _scene, _time: None
        self.worklist._idle = Mock()
        self.worklist._clean_action = lambda action: setattr(action, 'use_fake_user', True)
        self.worklist._composition_add_route = Mock(side_effect=AssertionError('Old catalog must not route Attach'))
        source._context_state = lambda _ctx: 'original context'
        source._restore_context = lambda _ctx, value, **_kw: self.events.append('restore context:' + value)
        source._import_action = self.import_action
        target.restore_import_preview = lambda _ctx, _rig, _action: self.events.append('restore import preview')
        target.attach_action_motion_data = lambda _rig, _action: self.events.append('bind original motion')
        target._action_motion_keys = lambda: ()
        self.worklist._capture_rig = lambda _ctx, rig: {'data': rig.data, 'fake_user': False,
            'action': rig.animation_data.action, 'properties': dict(rig)}
        self.worklist._restore_rig = self.restore_rig
        self.worklist._finish_rig = lambda *_args: self.events.append('finish token')
        self.worklist._linked_baseline = self.link_baseline
        self.native = SimpleNamespace(restore_owned=lambda *_args: self.events.append('close preview'),
            import_inputs=self.import_inputs, capture_for_action=self.capture_for_action,
            input_proof=self.input_proof, base_only=self.base_only)
        self.worklist._rigging_module = lambda *_args, **_kwargs: self.native
        self.capture = {'targetGuid': 'b' * 32, 'previewEnabled': False, 'newActionId': uuid.uuid4().hex}
        self.packet_path = self.folder / 'work_1' / 'actual_prepared.cdanim.json'
        self.packet_path.parent.mkdir()
        self.packet = {'targetGuid': 'b' * 32, 'rigging': self.capture, '_path': str(self.packet_path)}
        self.packet_path.write_text(json.dumps(self.capture), encoding='utf-8')
        self.packet['_sha256'] = self.links._sha256(self.packet_path)
        source._packet = self.packet_for_link
        source._source_model = lambda link, model: (Path(link.get('modelFile') or model).resolve(),
            self.links._sha256(Path(link.get('modelFile') or model)))
        self.work_id = uuid.uuid4().hex
        self.record = {'schema': self.workspace.SCHEMA, 'workspaceId': str(uuid.uuid4()),
            'targetGuid': 'b' * 32, 'targetName': 'Actual prepared Cosha', 'modelFile': str(self.model),
            'modelSha256': self.model_sha, 'prepareRequestId': uuid.uuid4().hex,
            'projectId': str(uuid.uuid4()), 'targetHash': 'c' * 32,
            'baseControllerGuid': 'd' * 32, 'avatarGuid': 'e' * 32, 'avatarHash': 'f' * 32,
            'preparedWorkItemIds': [self.work_id], 'clips': []}
        self.add_prepared(self.work_id, '1')
        self.manifest = self.folder / 'character_animation_workspace.json'
        self.write_workspace()

    def remove_ids(self, *, ids):
        self.removed.update(ids); self.ids.difference_update(ids)

    def action(self, name, handle, *, library=None):
        value = ID(name=name, library=library, slots=[SimpleNamespace(handle=handle, target_id_type='OBJECT')],
                   frame_range=(1.0, 61.0), use_fake_user=False)
        value.copy = lambda: self.copy_action(value)
        self.ids.add(value)
        return value

    def copy_action(self, value):
        result = self.action(value.name + ' copy', value.slots[0].handle)
        result.update(value); return result

    def add_prepared(self, work_id, suffix):
        folder = self.folder / ('work_' + suffix)
        folder.mkdir(exist_ok=True)
        path = folder / self.links.MANIFEST_NAME
        packet_path = folder / self.packet_path.name
        if packet_path != self.packet_path:
            packet_path.write_bytes(self.packet_path.read_bytes())
        row = {'workItemId': work_id, 'clipGuid': suffix * 32, 'clipLocalId': 9007199254740993,
               'clipName': 'Prepared input ' + suffix, 'linkManifest': str(path), 'needsRebuild': False}
        link = {'schema': self.links.SCHEMA, 'linkId': str(uuid.uuid4()), 'targetGuid': self.record['targetGuid'],
            **{key: row[key] for key in ('workItemId', 'clipGuid', 'clipLocalId')},
            'sourcePackage': str(packet_path), 'sourcePackageSha256': self.links._sha256(packet_path),
            'modelFile': str(self.model), 'modelSha256': self.model_sha}
        path.write_text(json.dumps(link), encoding='utf-8')
        self.record['clips'].append(row)
        return row, path

    def packet_for_link(self, link):
        packet = deepcopy(self.packet)
        packet['_path'] = str(Path(link['sourcePackage']).resolve())
        packet['_sha256'] = self.links._sha256(Path(link['sourcePackage']))
        return packet, 'Actual exported Rig'

    def write_workspace(self):
        self.manifest.write_text(json.dumps(self.record), encoding='utf-8')

    @contextmanager
    def base_only(self, _context, _rig):
        self.events.append('base-only enter')
        try:
            yield
        finally:
            self.events.append('base-only exit')

    def prepared(self):
        return self.worklist._attached_context(self.saved, str(self.manifest), None)

    def header(self):
        return tuple(getattr(self.saved, field) for field in ('workspace_path', 'workspace_identity',
            'workspace_id', 'target_guid', 'target_name', 'model_file', 'model_sha256'))

    def import_action(self, _context, rig, packet, start_frame, *, native_timebase=False):
        self.assertIs(rig, self.rig); self.assertTrue(native_timebase); self.assertEqual(start_frame, 1)
        self.events.append('native base import')
        action = self.action('Temporary actual Base', 42)
        action[self.links.PACKAGE_KEY], action[self.links.PACKAGE_HASH_KEY] = packet['_path'], packet['_sha256']
        return SimpleNamespace(action=action)

    def link_baseline(self, saved, baseline, item_id, _clip_name):
        self.events.append('source cache')
        self.cache_namespace = (saved.workspace_id, saved.model_sha256)
        self.cache_path = self.folder / (item_id + '.blend'); self.cache_path.write_bytes(b'fake native cache')
        linked = self.action('Read-only actual Source', baseline.slots[0].handle,
                             library=SimpleNamespace(filepath=str(self.cache_path)))
        linked.update(baseline)
        return linked, str(self.cache_path), self.links._sha256(self.cache_path)

    def import_inputs(self, _context, rig, packet, *, base_action, base_slot, work_item_id):
        self.events.append('native input import')
        self.assertIs(rig, self.rig); self.assertEqual(base_slot, 42); self.assertEqual(work_item_id, self.work_id)
        carrier = self.action('Owned twenty-channel input', 43)
        base_action[CAPTURE], base_action[INPUT] = deepcopy(packet['rigging']), carrier

    def capture_for_action(self, action, slot, work_id):
        self.assertEqual(slot, action.slots[0].handle); self.assertEqual(work_id, self.work_id)
        if CAPTURE not in action or INPUT not in action:
            raise ValueError('Missing authenticated capture/carrier')
        return deepcopy(action[CAPTURE])

    def input_proof(self, rig, action, slot, work_id):
        self.assertIs(rig, self.rig); self.capture_for_action(action, slot, work_id)
        return {'input_action': action[INPUT].name, 'input_slot': action[INPUT].slots[0].handle}

    def restore_rig(self, _context, rig, captured):
        self.events.append('restore original')
        rig.data, rig.animation_data.action = captured['data'], captured['action']
        rig.clear(); rig.update(captured['properties'])

    def add_actual(self):
        prepared = self.prepared()
        return self.worklist.add(self.context, self.worklist._clip_key(prepared['clip']),
                                 activate_new=False, _prepared_context=prepared)

    def session(self, item):
        return {'rig': self.rig, 'scene': self.scene, 'source_read_only': False,
            'base_action': item.custom_action, 'base_slot': item.custom_slot,
            'work_item_id': item.work_item_id, 'source_packet_sha256': self.packet['_sha256'],
            'input_action': item.custom_action[INPUT], 'input_slot': item.custom_action[INPUT].slots[0].handle,
            'capture': deepcopy(self.capture)}

    def test_current_request_requires_one_ready_rigging_work_not_choose_first(self):
        self.assertEqual(self.prepared()['clip']['workItemId'], self.work_id)
        second = uuid.uuid4().hex; self.add_prepared(second, '2')
        self.record['preparedWorkItemIds'].append(second); self.write_workspace()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.prepared()
        chosen = self.worklist._attached_context(self.saved, str(self.manifest), self.work_id)
        self.assertEqual(chosen['clip']['workItemId'], self.work_id)

    def test_empty_request_and_ordinary_packet_cannot_attach(self):
        self.record['preparedWorkItemIds'] = []; self.write_workspace()
        with self.assertRaisesRegex(ValueError, 'unique current'):
            self.prepared()
        self.record['preparedWorkItemIds'] = [self.work_id]; self.write_workspace()
        del self.packet['rigging']
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.prepared()
        self.assertEqual(len(self.saved.items), 9)

    def test_actual_typed_link_route_target_mismatch_is_nonmutating(self):
        path = Path(self.record['clips'][0]['linkManifest'])
        link = json.loads(path.read_text()); link['targetGuid'] = 'c' * 32
        path.write_text(json.dumps(link), encoding='utf-8')
        header, old_rows = self.header(), tuple(self.saved.items)
        with self.assertRaisesRegex(ValueError, 'disagree'):
            self.prepared()
        self.assertEqual(self.header(), header); self.assertEqual(tuple(self.saved.items), old_rows)

    def test_original_adoption_model_and_mapping_domain_reject_before_import(self):
        prepared = self.prepared(); original_header = self.header()
        other_model = self.folder / 'OtherModel.fbx'; other_model.write_bytes(self.model.read_bytes())
        for defect in ('adoption', 'setup', 'model', 'model-path', 'domain', 'controls'):
            with self.subTest(defect=defect):
                original_setup = self.scene.character_designer_setup.rig
                with patch.object(self.target, 'is_adopted', return_value=defect != 'adoption'), \
                        patch.object(self.ua, '_mapping', return_value=SimpleNamespace(
                            indices={'Hips': 0, 'RightHand': 1, 'OtherLeft': 2} if defect == 'domain'
                                else {name: 0 for name in self.mapping_names},
                            controls=None if defect == 'controls' else SimpleNamespace())):
                    self.scene.character_designer_setup.rig = None if defect == 'setup' else self.rig
                    changed = dict(prepared)
                    if defect == 'model': changed['model_sha256'] = '0' * 64
                    if defect == 'model-path': changed['model'] = other_model
                    with self.assertRaises(ValueError):
                        self.worklist._original_rigging_mapping(self.context, changed['packet'],
                            changed['model'], changed['model_sha256'])
                self.scene.character_designer_setup.rig = original_setup
        self.assertNotIn('native base import', self.events); self.assertEqual(self.header(), original_header)

    def test_add_preserves_old_nine_rows_header_actions_and_uses_new_cache_owner(self):
        header, old_rows, old_ids = self.header(), tuple(self.saved.items), set(self.ids)
        item = self.add_actual()
        self.assertEqual(self.header(), header); self.assertEqual(tuple(self.saved.items[:9]), old_rows)
        self.assertEqual(len(self.saved.items), 10); self.assertIs(item.rig, self.rig)
        self.assertIs(self.rig.data, self.original_data); self.assertIs(self.rig.animation_data.action, self.original_action)
        self.assertEqual(self.cache_namespace, (self.record['workspaceId'], self.model_sha))
        self.assertEqual(item.workspace_path, str(self.manifest))
        self.assertEqual(item.workspace_identity, self.worklist._identity(self.workspace.load_workspace(self.manifest)))
        self.assertNotEqual(item.workspace_identity, self.saved.workspace_identity)
        self.assertEqual(item.work_item_id, self.work_id); self.assertIn(CAPTURE, item.custom_action)
        self.assertFalse(item.new_animation)
        self.assertFalse(item.fingerprint_known); self.assertTrue(old_ids <= self.ids)
        self.worklist._composition_add_route.assert_not_called()
        self.assertLess(self.events.index('bind original motion'), self.events.index('native input import'))

    def test_public_attach_uses_actual_add_then_the_existing_activation_entry(self):
        header, old_rows = self.header(), tuple(self.saved.items)
        with patch.object(self.worklist, 'activate') as activate:
            item = self.worklist.attach_prepared_rigging_work(self.context, str(self.manifest))
        activate.assert_called_once_with(self.context, item.item_id, 'CUSTOM', _collection=False)
        self.assertEqual(self.header(), header); self.assertEqual(tuple(self.saved.items[:9]), old_rows)
        self.assertIs(item.rig, self.rig); self.assertIn(INPUT, item.custom_action)
        self.worklist._composition_add_route.assert_not_called()

    def test_existing_preserved_work_id_cannot_be_reassigned_to_the_new_workspace(self):
        self.saved.items[0].clip_key = self.worklist._clip_key(self.record['clips'][0])
        rows, header, ids = tuple(self.saved.items), self.header(), set(self.ids)
        with self.assertRaisesRegex(ValueError, 'another preserved row'):
            self.add_actual()
        self.assertEqual(tuple(self.saved.items), rows); self.assertEqual(self.header(), header)
        self.assertEqual(self.ids, ids); self.assertNotIn('native base import', self.events)

    def test_prepared_packet_changed_during_input_import_rolls_back_only_new_ids(self):
        header, rows, ids, props = self.header(), tuple(self.saved.items), set(self.ids), dict(self.rig)
        import_inputs = self.import_inputs
        def drift(*args, **kwargs):
            import_inputs(*args, **kwargs)
            self.packet_path.write_bytes(b'changed after imported owned carrier')
        with patch.object(self.native, 'import_inputs', side_effect=drift), self.assertRaisesRegex(ValueError, 'changed'):
            self.add_actual()
        self.assertEqual(self.header(), header); self.assertEqual(tuple(self.saved.items), rows)
        self.assertEqual(self.ids, ids); self.assertFalse(self.removed & ids)
        self.assertFalse(self.cache_path.exists()); self.assertEqual(dict(self.rig), props)
        self.assertIs(self.rig.data, self.original_data); self.assertIs(self.rig.animation_data.action, self.original_action)
        self.assertIn('restore context:original context', self.events)

    def test_input_import_failure_retains_original_rows_header_context_and_actions(self):
        header, rows, ids = self.header(), tuple(self.saved.items), set(self.ids)
        with patch.object(self.native, 'import_inputs', side_effect=ValueError('explicit input boundary failed')), \
                self.assertRaisesRegex(ValueError, 'input boundary'):
            self.add_actual()
        self.assertEqual(self.header(), header); self.assertEqual(tuple(self.saved.items), rows)
        self.assertEqual(self.ids, ids); self.assertFalse(self.removed & ids)
        self.assertIs(self.context.window_manager.character_designer_animation.target, self.rig)

    def test_per_item_route_uses_its_actual_target_keeps_legacy_global_target_guard(self):
        item = self.add_actual(); record = self.worklist._fresh_workspace(self.saved, item)
        link = self.links.load_link(item.manifest_path)
        self.worklist._validate_route(self.saved, record['clips'][0], link, item)
        self.assertNotEqual(link['targetGuid'], self.saved.target_guid)
        with self.assertRaisesRegex(ValueError, 'another work item'):
            self.worklist._validate_route(self.saved, record['clips'][0], link)
        changed = deepcopy(record['clips'][0]); changed['clipLocalId'] += 1
        with self.assertRaisesRegex(ValueError, 'own prepared workspace'):
            self.worklist._validate_route(self.saved, changed, link, item)

    def test_activate_missing_capture_rejects_before_preview_close_or_action_assignment(self):
        item = self.add_actual(); del item.custom_action[CAPTURE]
        action = self.rig.animation_data.action
        with patch.object(self.native, 'restore_owned') as close, patch.object(self.worklist, '_capture_rig') as capture:
            with self.assertRaisesRegex(ValueError, 'lost its local Custom base'):
                self.worklist.activate(self.context, item.item_id)
            close.assert_not_called(); capture.assert_not_called()
        self.assertIs(self.rig.animation_data.action, action); self.assertEqual(self.saved.active_index, 0)

    def test_activate_changed_workspace_rejects_before_any_native_transaction(self):
        item = self.add_actual(); self.record['targetGuid'] = 'c' * 32; self.write_workspace()
        with patch.object(self.native, 'restore_owned') as close, patch.object(self.worklist, '_capture_rig') as capture:
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                self.worklist.activate(self.context, item.item_id)
            close.assert_not_called(); capture.assert_not_called()
        self.assertIs(self.rig.animation_data.action, self.original_action)

    def test_validate_rigging_entry_exact_current_owner_slots_packet_and_capture(self):
        item = self.add_actual(); self.saved.active_index = 9
        valid = self.session(item); self.worklist.validate_rigging_entry(self.context, item, valid)
        changes = {'work_item_id': uuid.uuid4().hex, 'base_action': self.original_action, 'base_slot': 17,
                   'source_packet_sha256': 'e' * 64, 'input_action': self.original_action, 'input_slot': 17,
                   'source_read_only': True, 'capture': {**self.capture, 'targetGuid': 'e' * 32}}
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.worklist.validate_rigging_entry(self.context, item, {**valid, field: value})
        self.assertIs(self.rig.animation_data.action, self.original_action)

    def test_distinct_rna_wrappers_of_the_same_native_row_validate_without_python_identity(self):
        item = self.add_actual(); self.saved.active_index = 9
        wrapper = Row(**vars(item))
        self.assertIsNot(wrapper, item); self.assertEqual(wrapper.as_pointer(), item.as_pointer())
        self.worklist.validate_rigging_entry(self.context, wrapper, self.session(item))
        wrong_pointer = Row(**{**vars(item), '_native_ptr': item.as_pointer() + 1})
        with self.assertRaisesRegex(ValueError, 'exact Custom'):
            self.worklist.validate_rigging_entry(self.context, wrong_pointer, self.session(item))
        wrong_work = Row(**{**vars(item), 'work_item_id': uuid.uuid4().hex})
        with self.assertRaisesRegex(ValueError, 'exact Custom'):
            self.worklist.validate_rigging_entry(self.context, wrong_work, self.session(item))

    def test_closed_sync_missing_both_rigging_tags_rejects_before_ordinary_worker_fallback(self):
        item = self.add_actual(); self.saved.active_index = 9
        self.rig.animation_data.action = item.custom_action
        del item.custom_action[CAPTURE]; del item.custom_action[INPUT]
        with patch.object(self.worklist, '_rigging_module', return_value=None) as dispatch, \
                patch.object(self.worklist, '_association') as associate, \
                patch.object(self.links, 'begin_linked_export') as worker:
            with self.assertRaisesRegex(ValueError, 'lost its local Custom base'):
                self.worklist.sync(self.context)
            dispatch.assert_not_called(); associate.assert_not_called(); worker.assert_not_called()
        self.assertIs(self.rig.animation_data.action, item.custom_action)

    def test_paired_ordinary_packet_does_not_require_a_rigging_capture(self):
        item = self.add_actual(); self.saved.active_index = 9
        del self.packet['rigging']; del item.custom_action[CAPTURE]; del item.custom_action[INPUT]
        link, packet = self.worklist._rigging_entry_packet(self.context, item)
        self.assertNotIn('rigging', packet); self.assertEqual(link['workItemId'].replace('-', ''), self.work_id)

    def test_actual_registered_new_origin_keeps_paired_body_activate_and_new_sync_routes(self):
        # Run the unchanged public registration body and real new-manifest
        # writer/identity. Only original-template authentication is mocked.
        action = self.action('Real separate A Work Copy', 66)
        self.rig.animation_data.action, self.rig.animation_data.action_slot = action, action.slots[0]
        catalog = self.workspace.load_workspace(self.manifest)
        clip = catalog['clips'][0]; link = self.links.load_link(clip['linkManifest'])
        template = {'workspace_identity': self.worklist._identity(catalog)}
        origin = (catalog, SimpleNamespace(workspace_path=str(self.manifest)), clip, link,
                  {key: value for key, value in self.packet.items() if key != 'rigging'}, 'Actual exported Rig')
        with patch.object(self.new, '_origin_template', return_value=origin):
            item = self.new._register_origin(self.context, template)
        self.assertTrue(item.new_animation); self.assertEqual(item.clip_key, 'new:' + item.new_action_id)
        self.assertTrue(item.workspace_path); self.assertTrue(item.workspace_identity)
        self.assertEqual(self.new.load(item.manifest_path)['schema'], self.new.SCHEMA)
        self.assertEqual(len(self.saved.items), 10)
        with patch.object(self.worklist, '_rigging_module', return_value=None), \
                patch.object(self.worklist, '_rigging_entry_packet', side_effect=AssertionError('New Body is not a prepared clip')):
            self.assertIs(self.worklist.activate(self.context, item.item_id), action)
            marker = object()
            with patch.object(self.new, 'sync', return_value=marker) as sync_new:
                self.assertIs(self.worklist.sync(self.context), marker)
            sync_new.assert_called_once_with(self.context, item, collection=False, collection_proof=None)

    def test_forged_new_animation_flag_on_typed_rigging_link_still_rejects_real_new_schema_gate(self):
        item = self.add_actual(); self.saved.active_index = 9; item.new_animation = True
        self.rig.animation_data.action = item.custom_action
        action = self.rig.animation_data.action
        with self.assertRaisesRegex(ValueError, 'Unsupported new animation return schema'):
            self.worklist.activate(self.context, item.item_id)
        self.assertIs(self.rig.animation_data.action, action)
        with patch.object(self.links, 'begin_linked_export') as worker, \
                self.assertRaisesRegex(ValueError, 'Unsupported new animation return schema'):
            self.worklist.sync(self.context)
        worker.assert_not_called()

    def test_validate_rigging_entry_foreign_row_source_side_or_half_workspace_pair_reject(self):
        item = self.add_actual(); valid = self.session(item)
        with self.assertRaisesRegex(ValueError, 'exact Custom'):
            self.worklist.validate_rigging_entry(self.context, item, valid)
        self.saved.active_index = 9; item.side = 'SOURCE'
        with self.assertRaisesRegex(ValueError, 'exact Custom'):
            self.worklist.validate_rigging_entry(self.context, item, valid)
        item.side = 'CUSTOM'; item.workspace_identity = ''
        with self.assertRaisesRegex(ValueError, 'paired'):
            self.worklist.validate_rigging_entry(self.context, item, valid)


if __name__ == '__main__':
    unittest.main(verbosity=2)
