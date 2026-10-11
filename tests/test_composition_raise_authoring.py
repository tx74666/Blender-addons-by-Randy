"""Source-only authoring admission/input coverage, no native Blender claim."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import composition_raise_authoring as author


class RNAItems:
    """Reallocate all element wrappers on add, as native collection growth may."""
    def __init__(self, records):
        self.records, self.generation = list(records), 0

    def __iter__(self):
        return (RNARow(self, record) for record in self.records)

    def add(self, record):
        self.generation += 1
        self.records.append(record)
        return RNARow(self, record)


class RNARow:
    def __init__(self, collection, record):
        object.__setattr__(self, '_collection', collection)
        object.__setattr__(self, '_record', record)
        object.__setattr__(self, '_generation', collection.generation)

    def __getattr__(self, name):
        if self._generation != self._collection.generation:
            raise ReferenceError('Worklist row RNA was invalidated by collection growth')
        return self._record[name]

    def __setattr__(self, name, value):
        if self._generation != self._collection.generation:
            raise ReferenceError('Worklist row RNA was invalidated by collection growth')
        self._record[name] = value


class RaiseAuthoring(unittest.TestCase):
    def test_real_prepared_source_and_collected_publication_required(self):
        config = {'schema': author.SCHEMA, 'evidenceDirectory': str(Path.cwd()),
            'preparedRaiseWorkItemId': '22222222-2222-4222-8222-222222222222',
            'collectedHoldLoopWorkItemId': '11111111-1111-4111-8111-111111111111',
            'expectedModelSha256': 'a' * 64, 'rotationControl': None,
            'followingHoldPublication': {'manifestPath': str(Path.cwd() / 'Hold.link.json'),
                'manifestSha256': 'b' * 64, 'revision': 1,
                'fbxPath': str(Path.cwd() / 'Hold.fbx'), 'fbxSha256': 'c' * 64,
                'metadataPath': str(Path.cwd() / 'Hold.json'), 'metadataSha256': 'd' * 64}}
        self.assertIs(author.validate_config(config), config)
        for changed in ({**config, 'preparedRaiseWorkItemId': config['collectedHoldLoopWorkItemId']},
                        {**config, 'rotationControl': ['alias']},
                        {**config, 'followingHoldPublication': {}},
                        {**config, 'newActionId': config['preparedRaiseWorkItemId']}):
            with self.assertRaises(AssertionError):
                author.validate_config(changed)

    def test_only_exact_scope_channels_written_and_all_preflight_before_mutation(self):
        values = {'upper_rotation': [0., 0., 0.], 'foreign_lower': [7., 8., 9.]}
        rig = NS(path_resolve=lambda path: values[path])
        scope = NS(channels=(('upper_rotation', 0), ('upper_rotation', 2)))
        author._set_inputs(rig, scope, [.03, .07])
        self.assertEqual(values, {'upper_rotation': [.03, 0., .07], 'foreign_lower': [7., 8., 9.]})
        with self.assertRaises(AssertionError):
            author._set_inputs(rig, scope, [.2, float('nan')])
        self.assertEqual(values['upper_rotation'], [.03, 0., .07])
        with self.assertRaises(AssertionError):
            author._set_inputs(rig, scope, [.2])

    def test_native_key_calls_cover_every_declared_input_once(self):
        calls = []
        def key(**kwargs):
            calls.append(kwargs)
            return True
        channels = (('upper_rotation', 0), ('upper_rotation', 1), ('upper_scale', 2))
        author._key_inputs(NS(keyframe_insert=key), NS(channels=channels), 7.125)
        self.assertEqual(calls, [{'data_path': path, 'index': index, 'frame': 7.125} for path, index in channels])

    def test_failed_native_key_is_not_reported_as_authored(self):
        with self.assertRaisesRegex(AssertionError, 'native selected-slot FK key failed'):
            author._key_inputs(NS(keyframe_insert=lambda **kwargs: False),
                               NS(channels=(('upper_rotation', 0),)), 1.)

    def test_requested_control_must_be_exact_declared_visible_unlocked_rotation_input(self):
        bone = NS(rotation_mode='XYZ', path_from_id=lambda: 'pose.bones["native_FK"]',
                  bone=NS(hide=False, hide_select=False), lock_rotation=(False, False, False))
        rig = NS(pose=NS(bones={'native_FK': bone}))
        scope = NS(input_by_native={'upperarm.L': 'native_FK'},
                   channels=(('pose.bones["native_FK"].rotation_euler', 0),))
        self.assertEqual(author._rotation_control(rig, scope, 'native_FK'), 'native_FK')
        with self.assertRaises(AssertionError):
            author._rotation_control(rig, scope, 'upperarm.L')
        bone.lock_rotation = (True, False, False)
        with self.assertRaises(AssertionError):
            author._rotation_control(rig, scope, None)

    def test_public_NEW_ID_strings_exact_DTO_native_hex_separately_reported(self):
        work = '11111111-1111-4111-8111-111111111111'
        action = '22222222-2222-4222-8222-222222222222'
        row = NS(work_item_id=work.replace('-', ''), new_action_id=action.replace('-', ''),
                 item_id='native', manifest_path='actual', custom_slot=3)
        spec = {'editWorkItemId': work, 'editNewActionId': action}
        report = author._created_identity(row, spec, {'workItemId': work, 'newActionId': action})
        self.assertEqual(report['work_item_id'], work)
        self.assertEqual(report['new_action_id'], action)
        self.assertEqual(report['native_row_work_id'], work.replace('-', ''))
        with self.assertRaisesRegex(AssertionError, 'exact sealed DTO strings'):
            author._created_identity(row, spec, {'workItemId': work.replace('-', ''), 'newActionId': action})

    def _run_fixture(self, directory, *, tamper_packet=False, tamper=None):
        held_work, template_work, new_work, new_action = (str(uuid.uuid4()) for _ in range(4))
        hs = {'editWorkItemId': held_work, 'editNewActionId': '', 'timeMode': 'HoldLoop',
            'sampleStart': 0., 'sampleEnd': 2., 'editPhase': .19}
        rs = {'editWorkItemId': template_work, 'editNewActionId': '', 'timeMode': 'Raise',
            'holdWorkItemId': held_work, 'holdNewActionId': ''}
        def action(motion):
            value = NS(motion=motion, users=0, use_fake_user=False)
            value.copy = lambda: action(motion)
            return value
        rig = NS(animation_data=NS(action=None, action_slot=None))
        def record(item_id, work, spec, packet):
            manifest = directory+'/'+item_id+'.json'
            Path(manifest).write_text('original sealed manifest', encoding='utf-8')
            return dict(item_id=item_id, work_item_id=uuid.UUID(work).hex, new_action_id='',
                new_animation=False, rig=rig, model_sha256='a'*64, side='CUSTOM',
                custom_action=action(item_id), custom_slot=7, source_action=action(item_id+'Source'),
                source_slot=7, manifest_path=manifest, spec=spec, packet_sha=packet,
                packet_path=directory+'/'+item_id+'.cdanim.json', clip_key=item_id,
                link_identity='original sealed Link', model_file='actual model', export_rig_name='actual Rig',
                workspace_path='sealed workspace', workspace_identity='actual workspace',
                source_file=directory+'/'+item_id+'.blend', source_hash='e'*64,
                composition_json='original sealed DTO', composition_sha256='f'*64,
                application_role='actual Role', edit_scope='actual scope')
        held_record = record('native-held', held_work, hs, 'b'*64)
        template_record = record('native-template', template_work, rs, 'c'*64)
        items = RNAItems([held_record])
        saved = NS(items=items, rig=rig, active_index=0, status='prior', has_error=False,
            workspace_path='sealed workspace', workspace_identity='actual workspace',
            model_file='actual model', model_sha256='a'*64)
        context = NS(view_layer=NS(update=Mock()))
        def item_lookup(state, item_id):
            matches = [row for row in state.items if row.item_id == item_id]
            author.timing.require(len(matches) == 1 and matches[0].rig is state.rig, 'Official exact native row required')
            return matches[0]
        worklist = NS(state=lambda _context:saved, _item=item_lookup, _check_baseline=Mock(),
            activate=lambda _context,item_id,_side:item_lookup(saved,item_id), _clean_action=Mock(),
            _slot=lambda _action,handle=7:NS(handle=handle))
        def row_lookup(_context, work, _modules, _model):
            matches = [row for row in items if row.work_item_id == uuid.UUID(work).hex]
            if not matches:
                row = items.add(template_record)
                if tamper == 'SOURCE_add_Action': held_record['custom_action'] = action(held_record['custom_action'].motion)
                if tamper == 'SOURCE_add_spec': held_record['spec'] = {**hs, 'editPhase':.21}
                return row
            return matches[0]
        created = {'workItemId':new_work, 'newActionId':new_action}
        def register(_context, item_id):
            self.assertEqual(item_id, 'native-template')
            spec = {**deepcopy(rs), 'editWorkItemId':new_work, 'editNewActionId':new_action}
            new_record = {**template_record, 'item_id':'native-new', 'work_item_id':uuid.UUID(new_work).hex,
                'new_action_id':uuid.UUID(new_action).hex, 'new_animation':True,
                'custom_action':rig.animation_data.action, 'manifest_path':directory+'/new.json',
                'spec':spec, 'source_action':None}
            row = items.add(new_record)
            if tamper_packet: held_record['packet_sha'] = 'd'*64
            if tamper == 'NEW_add_Action': template_record['custom_action'] = action(template_record['custom_action'].motion)
            if tamper == 'NEW_add_spec': held_record['spec'] = {**hs, 'editPhase':.21}
            if tamper == 'NEW_add_slot': template_record['custom_slot'] = 8
            if tamper == 'NEW_add_manifest': Path(held_record['manifest_path']).write_text('coherent substituted manifest',encoding='utf-8')
            if tamper == 'NEW_add_Link': held_record['link_identity'] = 'coherent substituted Link'
            return row
        backend = NS(item_spec=lambda row:deepcopy(row.spec), _packet_for_item=lambda row:{'_sha256':row.packet_sha,'_path':row.packet_path},
            _prior=lambda _context,_rig:{'protected prior':True}, _restore_prior=Mock(), active=lambda _rig:False,
            _motion_hash=lambda value,_slot:value.motion, register_b=register,
            _host=lambda _name:NS(load=lambda _path:created))
        modules = {'animation_composition':backend, 'animation_worklist':worklist}
        config = {'schema': author.SCHEMA, 'evidenceDirectory': directory,
            'preparedRaiseWorkItemId': template_work, 'collectedHoldLoopWorkItemId': held_work,
            'expectedModelSha256': 'a'*64, 'rotationControl':None,
            'followingHoldPublication': {'manifestPath':directory+'/Hold.json', 'manifestSha256':'b'*64,
                'revision':1, 'fbxPath':directory+'/Hold.fbx', 'fbxSha256':'c'*64,
                'metadataPath':directory+'/Hold.meta.json', 'metadataSha256':'d'*64}}
        def authored(_context, row, held, _seconds, _modules, _requested):
            self.assertEqual((row.item_id, held.item_id), ('native-new', 'native-held'))
            return {'fresh_RNA_proof':True}
        patches = [patch.dict(sys.modules, {'bpy':NS(data=NS(actions=NS(remove=Mock())))}),
            patch.object(author.timing,'_modules',return_value=modules),
            patch.object(author.timing,'_row',side_effect=row_lookup),
            patch.object(author.timing,'_sealed_clocks'), patch.object(author.timing,'_published_hold',return_value={'actual':True}),
            patch.object(author.timing,'measure_registered_raise',return_value={'actual':True}),
            patch.object(author.timing,'_write'), patch.object(author,'_author',side_effect=authored)]
        return context, config, items, modules, patches

    def test_actual_run_reacquires_rows_after_SOURCE_import_and_NEW_collection_growth(self):
        with tempfile.TemporaryDirectory() as directory:
            context, config, items, _modules, patches = self._run_fixture(directory)
            original = next(iter(items))
            from contextlib import ExitStack
            with ExitStack() as stack:
                for replacement in patches: stack.enter_context(replacement)
                report = author.run(context, config)
            self.assertEqual(items.generation, 2)
            with self.assertRaises(ReferenceError): _ = original.item_id
            self.assertTrue(report['complete'])
            self.assertTrue(report['protected_Customs_Sources_packets_unchanged'])
            self.assertTrue(report['prior_state_restored'])

    def test_fresh_resolution_rejects_missing_or_duplicate_ID_without_import_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            context, _config, items, modules, _patches = self._run_fixture(directory)
            for item_id in ('missing', 'native-held'):
                if item_id == 'native-held': items.add(dict(items.records[0]))
                with self.assertRaisesRegex(AssertionError, 'missing or ambiguous'):
                    author._fresh_row(context, item_id, modules, 'a'*64)
            modules['animation_worklist']._check_baseline.assert_not_called()

    def test_actual_run_fresh_packet_protection_rejects_prior_packet_change_after_NEW_add(self):
        with tempfile.TemporaryDirectory() as directory:
            context, config, _items, _modules, patches = self._run_fixture(directory,tamper_packet=True)
            from contextlib import ExitStack
            with ExitStack() as stack:
                for replacement in patches: stack.enter_context(replacement)
                with self.assertRaisesRegex(AssertionError, 'original pre-add native Action/spec/route anchor'):
                    author.run(context, config)

    def test_original_anchors_reject_coherent_same_ID_Action_spec_slot_manifest_or_Link_substitution(self):
        for tamper in ('SOURCE_add_Action','SOURCE_add_spec','NEW_add_Action','NEW_add_spec',
                       'NEW_add_slot','NEW_add_manifest','NEW_add_Link'):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as directory:
                context, config, _items, _modules, patches = self._run_fixture(directory,tamper=tamper)
                from contextlib import ExitStack
                with ExitStack() as stack:
                    for replacement in patches: stack.enter_context(replacement)
                    with self.assertRaisesRegex(AssertionError, 'original pre-add native Action/spec/route anchor'):
                        author.run(context, config)


if __name__ == '__main__':
    unittest.main()
