"""Exercise canonical reference preparation/reuse with a native key-limit seam."""
import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class Rig(dict):
    def __bool__(self):
        return True

    def __setitem__(self, key, value):
        def check(mapping):
            for name, child in mapping.items():
                if len(name.encode('utf-8')) > 63:
                    raise KeyError('IDProperty key length is limited to 63 bytes')
                if isinstance(child, dict):
                    check(child)
        check({key: value})
        super().__setitem__(key, value)
        if getattr(self, '_after_write', None):
            self._after_write(key, value)


class Action(dict):
    __hash__ = object.__hash__

    def __eq__(self, other):
        return self is other

    def __getattribute__(self, name):
        if name in ('users', 'use_fake_user') and self.__dict__.get('_removed', False):
            raise ReferenceError('Removed native Action RNA')
        return super().__getattribute__(name)

    def __init__(self, name, digest, library=None):
        super().__init__()
        self.name, self.motion = name, digest
        self.library, self.users, self.use_fake_user = library, 0, False
        self.slots = [NS(handle=7, target_id_type='OBJECT')]


class Actions(list):
    def remove(self, action):
        super().remove(action)
        action._removed = True


class ReferenceReceiptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cd-reference-receipt-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        package_name = '_cd_reference_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        bpy.data = NS(actions=Actions(), libraries=NS(write=self.write_cache, load=self.load_cache))
        bpy.path = NS(abspath=lambda value:value)
        bpy.utils = NS(user_resource=lambda *_args,**_kwargs:str(self.root / 'caches'))
        module_patch = patch.dict(sys.modules, {package_name:package, 'bpy':bpy})
        module_patch.start(); self.addCleanup(module_patch.stop)
        self.bpy = bpy
        name = package_name + '.animation_composition'
        module_spec = importlib.util.spec_from_file_location(name, SOURCE / 'animation_composition.py')
        self.backend = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = self.backend
        module_spec.loader.exec_module(self.backend)
        self.rig = Rig()
        self.rig.data = NS(use_fake_user=False)
        self.prior = Action('Protected original B', 'f' * 64)
        self.prior.users = 1
        self.rig.animation_data = NS(action=self.prior)
        self.saved = NS(rig=self.rig)
        self.context = NS(scene=NS(unit_settings=NS(scale_length=1.)), view_layer=NS(update=Mock()))
        self.imported = None
        self.packet = None
        self.worklist = NS(state=lambda _context:self.saved, _idle=Mock(),
            _capture_rig=lambda _context,rig:{'data':rig.data, 'fake_user':rig.data.use_fake_user,
                'action':rig.animation_data.action}, _restore_rig=self.restore_rig,
            _clean_action=Mock(), _slot=lambda action:action.slots[0])
        self.ua = NS(load_package=lambda _path:self.packet, _mapping=Mock())
        self.target = NS(ensure_active=Mock(), restore_import_preview=Mock())
        self.source = NS(_context_state=lambda _context:{'owned':True}, _restore_context=Mock(),
            _import_action=self.import_action)
        self.links = NS(_sha256=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest())
        modules = {'animation_worklist':self.worklist, 'unity_animation':self.ua,
            'animation_worklist_target':self.target, 'animation_link_source':self.source,
            'animation_link':self.links}
        service_patch = patch.object(self.backend, '_host', side_effect=modules.__getitem__)
        service_patch.start(); self.addCleanup(service_patch.stop)
        hash_patch = patch.object(self.backend, '_motion_hash',
            side_effect=lambda action,slot:action.motion if slot == action.slots[0].handle else 'wrong native slot')
        hash_patch.start(); self.addCleanup(hash_patch.stop)

    def spec(self, digest='1' * 64):
        packet_path = self.root / (digest + '.cdanim.json')
        self.packet = {'_path':str(packet_path), '_sha256':digest, 'clipGuid':'3'*32,
            'clipLocalId':42, 'clipName':'Actual readonly A'}
        contract = self.backend.contract
        return contract.validate(dict(schema=contract.SCHEMA, compositionId='2'*32,
            applicationRole=contract.ROLE, baseClipGuid='3'*32, baseClipLocalId=42,
            baseClipSha256='4'*64, referencePacketFile=str(packet_path), referencePacketSha256=digest,
            editWorkItemId='5'*32, editNewActionId='', mask='UpperBody', blendMode='Override', weight=1,
            humanRoles=['Chest'], allowedBonePaths=['Rig/Hips/Spine/Chest'], editableControlIds=['chest'],
            sourceRestSha256='6'*64, timeMode='HoldLoop', referenceSpeed=1, editSpeed=1,
            referencePhase=0, editPhase=0, fps=30, sampleStart=0, sampleEnd=1,
            holdWorkItemId='', holdNewActionId='', weaponGuid='', weaponPrefabGuid='',
            weaponModelFile='', weaponModelSha256='', weaponSocketPath='', weaponRigBonePath='',
            weaponLocalMatrix=None, weaponPrefabLocalMatrix=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.],
            weaponContextFile=str(self.root/'weapon.json'), weaponContextSha256='7'*64, gripTargets=[]))

    def import_action(self, _context, rig, _packet, _frame):
        self.imported = Action('Imported actual A', '8'*64)
        self.bpy.data.actions.append(self.imported)
        rig.animation_data.action = self.imported
        return NS(action=self.imported)

    def restore_rig(self, _context, rig, captured):
        rig.animation_data.action = captured['action']

    def write_cache(self, path, actions, **_kwargs):
        self.assertEqual(len(actions), 1)
        Path(path).write_bytes(b'owned native cache bytes')

    @contextmanager
    def load_cache(self, path, **_kwargs):
        selected = NS(actions=[])
        yield NS(actions=[]), selected
        linked = Action(selected.actions[0], self.imported.motion, NS(filepath=path))
        linked.update(self.imported)
        self.bpy.data.actions.append(linked)
        selected.actions = [linked]

    def prepare(self, digest='1'*64):
        spec = self.spec(digest)
        item = NS(rig=self.rig, work_item_id=uuid.UUID(spec['editWorkItemId']).hex, new_action_id='')
        return spec, self.backend.prepare_reference(self.context, item, spec)

    def test_full_digest_is_lossless_ascii_under_native_key_limit(self):
        for digest in ('0'*64, 'f'*64, bytes(range(32)).hex(), hashlib.sha256(b'actual A packet').hexdigest()):
            key = self.backend.reference_receipt_key(digest)
            self.assertEqual(len(key.encode('ascii')), 59)
            payload = key[len(self.backend.REFERENCE_RECEIPT_PREFIX):]
            self.assertEqual(base64.b32decode(payload + '='*4).hex(), digest)
            self.assertEqual(self.backend.reference_receipt_key(digest.upper()), key)
        first, second = '1'*63+'0', '1'*63+'1'
        self.assertNotEqual(self.backend.reference_receipt_key(first), self.backend.reference_receipt_key(second))

    def test_malformed_or_shortened_digest_rejects_before_lookup(self):
        for value in ('', '1'*63, '1'*65, 'g'*64, '1'*32+' '+'1'*31, None, b'1'*64):
            with self.assertRaises(ValueError): self.backend.reference_receipt_key(value)
            with self.assertRaises(ValueError): self.backend.reference_receipt(self.rig, value)

    def test_actual_prepare_passes_enforced_63_byte_limit_and_reuses_exact_native_cache(self):
        with self.assertRaises(KeyError): self.rig[self.backend.REFERENCE_OWNER] = {'1'*64:'old invalid key'}
        spec, (linked, slot) = self.prepare()
        receipt = self.backend.reference_receipt(self.rig, spec['referencePacketSha256'])
        self.assertEqual(receipt['packet_sha256'], spec['referencePacketSha256'])
        self.assertEqual(receipt['slot'], slot)
        self.assertEqual(receipt['motion_sha256'], linked.motion)
        self.assertEqual(receipt['cache_sha256'], self.links._sha256(receipt['cache']))
        self.assertIs(self.rig.animation_data.action, self.prior)
        self.assertFalse(self.rig.data.use_fake_user)
        self.assertEqual(self.backend.find_reference(self.context, spec), (linked, slot))
        with patch.object(self.source, '_import_action', side_effect=AssertionError('No reference reimport')):
            item = NS(rig=self.rig, work_item_id=uuid.UUID(spec['editWorkItemId']).hex, new_action_id='')
            self.assertEqual(self.backend.prepare_reference(self.context, item, spec), (linked, slot))

    def test_two_digests_differing_only_at_last_bit_keep_distinct_complete_receipts(self):
        first, second = '1'*63+'0', '1'*63+'1'
        a, (action_a, slot_a) = self.prepare(first)
        b, (action_b, slot_b) = self.prepare(second)
        self.assertEqual(len(self.rig[self.backend.REFERENCE_OWNER]), 2)
        self.assertEqual(self.backend.find_reference(self.context, a), (action_a, slot_a))
        self.assertEqual(self.backend.find_reference(self.context, b), (action_b, slot_b))

    def test_missing_receipt_returns_None_but_malformed_wrong_SHA_or_schema_reject(self):
        digest = '1'*64
        self.assertIsNone(self.backend.reference_receipt(self.rig, digest))
        key = self.backend.reference_receipt_key(digest)
        for value in (None, 'invalid JSON', '[]', '', json.dumps({'schema':'wrong', 'packet_sha256':digest}),
            json.dumps({'schema':'cdesigner.composition-reference/1', 'packet_sha256':'1'*63+'2'})):
            self.rig[self.backend.REFERENCE_OWNER] = {key:value}
            with self.assertRaises(ValueError): self.backend.reference_receipt(self.rig, digest)

    def test_actual_reuse_rejects_cache_or_motion_or_slot_or_library_tamper(self):
        for field in ('cache', 'motion', 'slot', 'library'):
            with self.subTest(field=field):
                self.rig.pop(self.backend.REFERENCE_OWNER, None)
                self.bpy.data.actions.clear()
                spec, (action, _slot) = self.prepare()
                receipt = self.backend.reference_receipt(self.rig, spec['referencePacketSha256'])
                if field == 'cache': Path(receipt['cache']).write_bytes(b'changed native bytes')
                elif field == 'motion': action.motion = '9'*64
                elif field == 'library': action.library.filepath = str(self.root/'foreign.blend')
                else:
                    proof = json.loads(action[self.backend.REFERENCE_OWNER])
                    proof['slot'] = 8
                    action[self.backend.REFERENCE_OWNER] = json.dumps(proof)
                    receipt['slot'] = 8
                    self.rig[self.backend.REFERENCE_OWNER][self.backend.reference_receipt_key(spec['referencePacketSha256'])] = json.dumps(receipt)
                with self.assertRaises(ValueError): self.backend.find_reference(self.context, spec)

    def test_ambiguous_matching_native_Action_rejects_without_name_guess(self):
        spec, (action, _slot) = self.prepare()
        duplicate = Action('Another name', action.motion, action.library)
        duplicate.update(action)
        self.bpy.data.actions.append(duplicate)
        with self.assertRaisesRegex(ValueError, 'ambiguous'): self.backend.find_reference(self.context, spec)

    def test_corrupt_existing_receipt_does_not_reimport_or_replace_owned_cache(self):
        spec, (_action, _slot) = self.prepare()
        self.rig[self.backend.REFERENCE_OWNER][self.backend.reference_receipt_key(spec['referencePacketSha256'])] = 'malformed'
        files = {path.name:path.read_bytes() for path in (self.root/'caches').iterdir()}
        item = NS(rig=self.rig, work_item_id=uuid.UUID(spec['editWorkItemId']).hex, new_action_id='')
        with patch.object(self.source, '_import_action', side_effect=AssertionError('Corruption cannot authorize a cache replacement')):
            with self.assertRaises(ValueError): self.backend.prepare_reference(self.context, item, spec)
        self.assertEqual({path.name:path.read_bytes() for path in (self.root/'caches').iterdir()}, files)
        self.assertIs(self.rig.animation_data.action, self.prior)

    def test_native_packet_path_and_complete_owner_proof_are_not_name_authorization(self):
        spec, (action, _slot) = self.prepare()
        proof = json.loads(action[self.backend.REFERENCE_OWNER])
        proof['packet_path'] = str(self.root/'foreign_packet.cdanim.json')
        action[self.backend.REFERENCE_OWNER] = json.dumps(proof)
        receipt = self.backend.reference_receipt(self.rig, spec['referencePacketSha256'])
        receipt['packet_path'] = proof['packet_path']
        self.rig[self.backend.REFERENCE_OWNER][self.backend.reference_receipt_key(spec['referencePacketSha256'])] = json.dumps(receipt)
        with self.assertRaises(ValueError): self.backend.find_reference(self.context, spec)

    def files(self):
        folder = self.root/'caches'
        return {path.name:path.read_bytes() for path in folder.iterdir()} if folder.exists() else {}

    def item(self, spec):
        return NS(rig=self.rig, work_item_id=uuid.UUID(spec['editWorkItemId']).hex, new_action_id='')

    def test_no_Action_malformed_wrong_SHA_or_valid_orphan_receipt_never_reimports(self):
        spec, (action, _slot) = self.prepare()
        self.bpy.data.actions.remove(action)
        key = self.backend.reference_receipt_key(spec['referencePacketSha256'])
        valid = self.rig[self.backend.REFERENCE_OWNER][key]
        for raw in ('malformed', None, json.dumps({'schema':'cdesigner.composition-reference/1',
                'packet_sha256':'a'*64}), valid):
            with self.subTest(receipt=raw):
                self.rig[self.backend.REFERENCE_OWNER][key] = raw
                before, files = deepcopy(dict(self.rig[self.backend.REFERENCE_OWNER])), self.files()
                with patch.object(self.source, '_import_action', side_effect=AssertionError('An orphan cannot authorize reimport')):
                    with self.assertRaises(ValueError):
                        self.backend.prepare_reference(self.context, self.item(spec), spec)
                self.assertEqual(dict(self.rig[self.backend.REFERENCE_OWNER]), before)
                self.assertEqual(self.files(), files)
                self.assertIs(self.rig.animation_data.action, self.prior)

    def test_matching_Action_without_receipt_and_invalid_inventory_reject_before_import(self):
        spec, (action, _slot) = self.prepare()
        self.rig.pop(self.backend.REFERENCE_OWNER)
        files = self.files()
        with patch.object(self.source, '_import_action', side_effect=AssertionError('No implicit receipt recovery')):
            with self.assertRaises(ValueError): self.backend.prepare_reference(self.context, self.item(spec), spec)
        self.assertNotIn(self.backend.REFERENCE_OWNER, self.rig)
        self.assertEqual(self.files(), files)
        self.bpy.data.actions.remove(action)
        for raw in (None, [], 'invalid inventory'):
            self.rig[self.backend.REFERENCE_OWNER] = raw
            with patch.object(self.source, '_import_action', side_effect=AssertionError('Malformed inventory cannot authorize import')):
                with self.assertRaises(ValueError): self.backend.prepare_reference(self.context, self.item(spec), spec)
            self.assertEqual(self.rig[self.backend.REFERENCE_OWNER], raw)
            self.assertEqual(self.files(), files)

    def _prior_inventory(self, kind):
        self.rig.pop(self.backend.REFERENCE_OWNER, None)
        self.bpy.data.actions.clear()
        for path in (self.root/'caches').glob('*'):
            path.unlink()
        if kind == 'empty':
            self.rig[self.backend.REFERENCE_OWNER] = {}
        elif kind == 'other':
            self.prepare('2'*64)
        return (self.backend.REFERENCE_OWNER in self.rig,
            deepcopy(dict(self.rig.get(self.backend.REFERENCE_OWNER, {}))), self.files(), tuple(self.bpy.data.actions))

    def _assert_prior_inventory(self, before):
        present, inventory, files, actions = before
        self.assertEqual(self.backend.REFERENCE_OWNER in self.rig, present)
        self.assertEqual(dict(self.rig.get(self.backend.REFERENCE_OWNER, {})), inventory)
        self.assertEqual(self.files(), files)
        self.assertEqual(tuple(self.bpy.data.actions), actions)
        self.assertIs(self.rig.animation_data.action, self.prior)
        self.assertFalse(self.rig.data.use_fake_user)

    def test_persistent_context_restore_failure_keeps_absent_empty_and_existing_inventory(self):
        for kind in ('absent', 'empty', 'other'):
            with self.subTest(kind=kind):
                before = self._prior_inventory(kind)
                spec = self.spec()
                self.source._restore_context.side_effect = RuntimeError('primary context restore failure')
                with self.assertRaisesRegex(RuntimeError, 'primary context restore failure') as error:
                    self.backend.prepare_reference(self.context, self.item(spec), spec)
                self.source._restore_context.side_effect = None
                self._assert_prior_inventory(before)
                self.assertTrue(any('recovery also failed' in note for note in error.exception.__notes__))

    def test_imported_Action_remove_failure_before_or_after_removal_keeps_exact_inventory(self):
        for kind in ('absent', 'empty', 'other'):
            for after in (False, True):
                with self.subTest(kind=kind, after=after):
                    before = self._prior_inventory(kind)
                    spec = self.spec()
                    original_remove = self.bpy.data.actions.remove
                    failed = False
                    def remove(action):
                        nonlocal failed
                        if action is self.imported and not failed:
                            failed = True
                            if after: original_remove(action)
                            raise RuntimeError('primary native remove failure')
                        original_remove(action)
                    with patch.object(self.bpy.data.actions, 'remove', side_effect=remove):
                        with self.assertRaisesRegex(RuntimeError, 'primary native remove failure'):
                            self.backend.prepare_reference(self.context, self.item(spec), spec)
                    self._assert_prior_inventory(before)

    def test_partially_written_publication_rolls_back_before_persistent_recovery_failure(self):
        for kind in ('absent', 'empty', 'other'):
            with self.subTest(kind=kind):
                before = self._prior_inventory(kind)
                spec = self.spec()
                key = self.backend.reference_receipt_key(spec['referencePacketSha256'])
                def fail_write(name, value):
                    if name == self.backend.REFERENCE_OWNER and key in value:
                        self.rig._after_write = None
                        raise RuntimeError('primary publication failure after assignment')
                self.rig._after_write = fail_write
                self.source._restore_context.side_effect = [None, RuntimeError('secondary context restore failure')]
                with self.assertRaisesRegex(RuntimeError, 'primary publication failure after assignment') as error:
                    self.backend.prepare_reference(self.context, self.item(spec), spec)
                self.source._restore_context.side_effect = None
                self.rig._after_write = None
                self._assert_prior_inventory(before)
                self.assertTrue(any('secondary context restore failure' in note for note in error.exception.__notes__))

    def test_success_publishes_after_context_and_native_Action_cleanup(self):
        spec = self.spec()
        def observe_write(name, value):
            if name == self.backend.REFERENCE_OWNER:
                self.assertIs(self.rig.animation_data.action, self.prior)
                self.assertNotIn(self.imported, self.bpy.data.actions)
                self.assertEqual(self.source._restore_context.call_count, 1)
                self.assertFalse(self.rig.data.use_fake_user)
        self.rig._after_write = observe_write
        action, slot = self.backend.prepare_reference(self.context, self.item(spec), spec)
        self.rig._after_write = None
        self.assertEqual(self.backend.find_reference(self.context, spec), (action, slot))


if __name__ == '__main__':
    unittest.main()
