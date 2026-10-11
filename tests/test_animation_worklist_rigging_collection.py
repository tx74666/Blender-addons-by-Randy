"""Fixed Target/Hint ownership and frozen-return guards, without native execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


TESTS = Path(__file__).resolve().parent


def fixture_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, TESTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = fixture_module('test_animation_worklist_collection.py', '_rigging_collection_legacy_fixture')
wire = fixture_module('test_animation_rigging_contract.py', '_rigging_collection_wire_fixture')


class OwnedAction(dict):
    def __init__(self, name, properties=None):
        super().__init__(properties or {})
        self.name, self.library = name, None


class RiggingCollectionTests(unittest.TestCase):
    def setUp(self):
        # Reuse native-boundary stubs without inheriting/rerunning the legacy suite.
        self.fixture = legacy.CollectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.collection, self.item = f.collection, f.item
        self.capture = wire.capture()
        self.item.custom_action = OwnedAction('Base Walk', {
            self.collection.RIGGING_CAPTURE_KEY: json.dumps(self.capture),
            self.collection.RIGGING_INPUT_REF: object()})
        self.item.source_action, self.item.source_slot = OwnedAction('Linked Source Walk'), 3
        self.item.work_item_id = '5' * 32
        for field in ('b_edited', 'effective_inputs_changed', 'composition_changed', 'export_needed'):
            setattr(self.item, field, False)
        f.link.update(workItemId=self.item.work_item_id, targetGuid=self.capture['targetGuid'])
        self.item.link_identity = json.dumps(f.link)
        self.component = {'schema': self.collection.RIGGING_INPUT_SCHEMA,
            'source_packet_sha256': 'a' * 64, 'work_item_id': self.item.work_item_id,
            'new_action_id': self.capture['newActionId'],
            'capture_sha256': wire.contract.capture_digest(self.capture),
            'base_action': self.item.custom_action.name, 'base_slot': self.item.custom_slot,
            'input_action': 'Arm Targets', 'input_slot': 7, 'source_read_only': False,
            'initial_input_motion': 'c' * 64, 'input_motion_sha256': 'c' * 64}
        self.current_motion = 'c' * 64
        package = self.collection.__package__
        native = ModuleType(package + '.unity_animation_rigging')
        native.input_proof = Mock(side_effect=self.input_proof)
        native.capture_for_action = Mock(side_effect=lambda *_args: deepcopy(self.capture))
        modules = patch.dict(sys.modules, {native.__name__: native,
            package + '.animation_rigging_contract': wire.contract})
        modules.start()
        self.addCleanup(modules.stop)
        setattr(sys.modules[package], 'unity_animation_rigging', native)
        setattr(sys.modules[package], 'animation_rigging_contract', wire.contract)
        self.native = native
        f.links.ANIMATION_METADATA_MAX_BYTES = 32 * 1024 * 1024

    def input_proof(self, rig, base, slot, work_id, *, source=False):
        self.assertIs(rig, self.item.rig)
        self.assertIs(base, self.item.custom_action)
        self.assertEqual((slot, work_id), (self.item.custom_slot, self.item.work_item_id))
        row = deepcopy(self.component)
        row['input_motion_sha256'] = row['initial_input_motion'] if source else self.current_motion
        return row

    def baseline(self):
        self.collection.remember_source_baseline(self.fixture.context, self.item)
        return json.loads(self.item.source_baseline_receipt)

    def proof(self):
        return self.collection.item_fingerprint(self.fixture.context, self.item)

    def returned(self):
        row = self.component
        return wire.contract.build_export(self.capture, wire.samples(self.capture),
            source_packet_sha256=row['source_packet_sha256'], work_item_id=row['work_item_id'],
            new_action_id=row['new_action_id'], source_action=row['base_action'],
            source_action_slot=row['base_slot'], target_input_action=row['input_action'],
            target_input_action_slot=row['input_slot'])

    def metadata(self):
        return {'action': self.component['base_action'], 'actionSlot': self.component['base_slot'],
                'rigging': self.returned()}

    def write_metadata(self, value):
        f = self.fixture
        f.metadata.write_text(json.dumps(value), encoding='utf-8')
        f.link['metadataSha256'] = f.digest(f.metadata)
        self.item.link_identity = json.dumps(f.link)

    def job(self, proof=None):
        return {'_worklist_scene': self.fixture.context.scene,
            '_worklist_item_id': self.item.item_id,
            '_worklist_fingerprint': deepcopy(self.proof() if proof is None else proof),
            '_worklist_export_implementation': deepcopy(self.fixture.implementation)}

    def test_initial_inputs_match_source_baseline_without_action_activation(self):
        baseline = self.baseline()
        self.assertEqual(baseline['proof']['base_fingerprint'], 'a' * 64)
        self.assertEqual(baseline['proof']['rigging']['input_motion_sha256'], 'c' * 64)
        self.assertFalse(baseline['proof']['rigging']['source_read_only'])
        self.native.input_proof.assert_called_once_with(self.item.rig, self.item.custom_action,
            self.item.custom_slot, self.item.work_item_id, source=True)
        self.fixture.fingerprints.fingerprint_native.assert_called_with(
            self.item.source_action, self.item.rig, self.fixture.context.scene, self.item.source_slot)
        active = self.item.rig.animation_data.action
        self.collection.scan_changes(self.fixture.context)
        self.assertEqual(self.item.scan_state, 'UNCHANGED')
        self.assertFalse(self.item.sync_selected)
        self.assertIs(self.item.rig.animation_data.action, active)
        self.fixture.worklist.activate.assert_not_called()

    def test_target_only_edit_changes_combined_fingerprint_without_reseeding(self):
        self.baseline()
        frozen = self.item.source_baseline_receipt
        initial = self.proof()
        self.current_motion = 'd' * 64
        actual = self.proof()
        self.assertEqual(initial['base_fingerprint'], actual['base_fingerprint'])
        self.assertEqual(actual['rigging']['initial_input_motion'], 'c' * 64)
        self.assertNotEqual(initial['fingerprint'], actual['fingerprint'])
        self.assertFalse(self.collection._same_proof(initial, actual))
        self.collection.scan_changes(self.fixture.context)
        self.assertEqual(self.item.scan_state, 'CHANGED')
        self.assertTrue(self.item.export_needed)
        self.assertTrue(self.item.effective_inputs_changed)
        self.assertTrue(self.item.sync_selected)
        self.assertEqual(self.item.source_baseline_receipt, frozen)

    def test_target_proof_never_promotes_unknown_base_or_selects_it_automatically(self):
        self.fixture.proof.update(fingerprint_known=False, fingerprint='', proof_reason='Unproven constraints')
        self.baseline()
        self.current_motion = 'd' * 64
        proof = self.proof()
        self.assertFalse(proof['fingerprint_known'])
        self.assertEqual(proof['fingerprint'], '')
        self.collection.scan_changes(self.fixture.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertIn('Target inputs changed', self.item.scan_reason)
        self.assertTrue(self.item.export_needed)
        self.assertFalse(self.item.sync_selected)
        self.item.sync_selected = True  # Explicit manual selection survives rescan.
        self.collection.scan_changes(self.fixture.context)
        self.assertTrue(self.item.sync_selected)

    def test_fixed_header_slots_and_component_digest_fail_closed(self):
        good = self.proof()
        for field, value in (('base_slot', True), ('input_slot', -1), ('input_slot', 2**31),
            ('input_action', self.component['base_action']), ('work_item_id', 'bad'),
            ('input_motion_sha256', 'bad'), ('unexpected_graph', 'anything')):
            altered = deepcopy(good)
            altered['rigging'][field] = value
            self.assertFalse(self.collection._valid_proof(altered), field)
        altered = deepcopy(good)
        altered['rigging']['input_motion_sha256'] = 'd' * 64
        self.assertFalse(self.collection._valid_proof(altered))
        altered = deepcopy(good)
        del altered['rigging']
        self.assertFalse(self.collection._valid_proof(altered))

    def test_tampered_immutable_capture_cannot_supply_an_unchanged_proof(self):
        self.baseline()
        self.capture['frames'][0]['controls'][0]['targetPosition'][0] += 1
        self.assertIsNone(self.collection.comparison_receipt(
            self.item, self.fixture.link, self.fixture.implementation))
        self.collection.scan_changes(self.fixture.context)
        self.assertEqual(self.item.scan_state, 'UNKNOWN')
        self.assertFalse(self.item.sync_selected)

    def test_publication_keeps_launch_motion_when_later_target_edits_exist(self):
        launch = self.proof()
        self.write_metadata(self.metadata())
        job = self.job(launch)
        self.current_motion = 'd' * 64
        self.assertEqual(self.collection.export_finished(job, self.fixture.publication()), '')
        receipt = json.loads(self.item.last_synced_receipt)
        self.assertEqual(receipt['proof'], launch)
        self.assertEqual(receipt['rigging_metadata'], self.returned())
        self.collection.scan_changes(self.fixture.context)
        self.assertEqual(self.item.scan_state, 'CHANGED')

    def test_physical_return_must_match_both_slots_and_frozen_packet_owner(self):
        launch = self.proof()
        valid = self.metadata()
        mutations = [('actionSlot', 99), ('actionSlot', True), ('sourcePacketSha256', 'b' * 64),
            ('workItemId', '6' * 32), ('newActionId', '7' * 32), ('sourceActionSlot', 99),
            ('targetInputActionSlot', 99), ('targetInputAction', 'Foreign inputs')]
        for field, value in mutations:
            with self.subTest(field=field):
                altered = deepcopy(valid)
                if field == 'actionSlot':
                    altered[field] = value
                else:
                    altered['rigging'][field] = value
                self.write_metadata(altered)
                with self.assertRaises(ValueError):
                    self.collection._published_rigging(self.item, self.fixture.link, launch)
        altered = deepcopy(valid)
        altered['rigging']['frames'][0]['controls'][0]['targetParentRoot'] = wire.MATRIX
        self.write_metadata(altered)
        with self.assertRaises(ValueError):
            self.collection._published_rigging(self.item, self.fixture.link, launch)

    def test_failed_return_guard_preserves_prior_receipt(self):
        self.item.last_synced_receipt = 'previous receipt is preserved'
        metadata = self.metadata()
        metadata['actionSlot'] += 1
        self.write_metadata(metadata)
        error = self.collection.export_finished(self.job(), self.fixture.publication())
        self.assertIn('base Action/slot', error)
        self.assertEqual(self.item.last_synced_receipt, 'previous receipt is preserved')

    def test_valid_receipt_rejects_later_physical_return_or_capture_tampering(self):
        self.write_metadata(self.metadata())
        self.assertEqual(self.collection.export_finished(self.job(), self.fixture.publication()), '')
        self.assertIsNotNone(self.collection.comparison_receipt(
            self.item, self.fixture.link, self.fixture.implementation))
        self.capture['frames'][0]['controls'][0]['hintPosition'][0] += .1
        self.assertIsNone(self.collection.comparison_receipt(
            self.item, self.fixture.link, self.fixture.implementation))

    def test_legacy_ordinary_row_keeps_its_base_only_proof(self):
        self.item.custom_action = OwnedAction('Ordinary FK')
        actual = self.proof()
        self.assertEqual(actual, self.fixture.proof)
        self.assertNotIn('rigging', actual)
        self.baseline()
        self.collection.scan_changes(self.fixture.context)
        self.assertEqual(self.item.scan_state, 'UNCHANGED')
        self.native.input_proof.assert_not_called()
        self.native.capture_for_action.assert_not_called()

    def test_export_semantics_pin_complete_rigging_pair_and_explicit_absence(self):
        folder = self.fixture.folder / 'implementation'
        folder.mkdir()
        (folder / 'animation_worklist_collection.py').write_bytes(b'collection implementation')
        for name in ('animation_export.py', 'animation_export_worker.py',
                     'unity_export_worker.py', 'animation_worklist_fingerprint.py'):
            (folder / name).write_bytes(name.encode('utf-8'))
        with patch.object(self.collection, '__file__', str(folder / 'animation_worklist_collection.py')):
            absent = self.fixture.real_export_implementation()
            self.assertEqual(absent['unity_animation_rigging.py'], 'absent')
            self.assertEqual(absent['animation_rigging_contract.py'], 'absent')
            (folder / 'animation_rigging_contract.py').write_bytes(b'protocol')
            with self.assertRaisesRegex(ValueError, 'pair is incomplete'):
                self.fixture.real_export_implementation()
            (folder / 'unity_animation_rigging.py').write_bytes(b'fixed native inputs')
            complete = self.fixture.real_export_implementation()
            self.assertNotEqual(complete['animation_rigging_contract.py'], 'absent')
            (folder / 'unity_animation_rigging.py').write_bytes(b'changed native inputs')
            changed = self.fixture.real_export_implementation()
            self.assertNotEqual(complete['unity_animation_rigging.py'], changed['unity_animation_rigging.py'])


if __name__ == '__main__':
    unittest.main()
