"""Real Link/Worklist/Workspace boundaries; no Blender or process starts."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]


class Native(dict):
    def __init__(self, **attrs):
        super().__init__()
        self.__dict__.update(attrs)


class LinkCompositionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='composition-link-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.package_name = '_composition_link_' + uuid.uuid4().hex
        self.package = ModuleType(self.package_name)
        self.package.__path__ = [str(SOURCE)]
        stubs = {self.package_name: self.package, 'bpy': ModuleType('bpy')}
        for name in ('animation_link_source', 'unity_animation', 'animation_export'):
            stub = ModuleType(self.package_name + '.' + name)
            stubs[stub.__name__] = stub
            setattr(self.package, name, stub)
        self.modules = patch.dict(sys.modules, stubs)
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.link, self.worklist = self.load('animation_link'), self.load('animation_worklist')
        self.contract = self.load('animation_composition_contract')
        self.load('animation_workspace')
        self.packet = self.root / 'B.cdanim.json'; self.packet.write_bytes(b'Authentic sealed packet')
        self.model = self.root / 'Cosha.fbx'; self.model.write_bytes(b'Authentic model')
        self.path = self.root / self.link.MANIFEST_NAME
        self.spec = {key: '' for key in self.contract.FIELDS}
        self.spec.update(schema=self.contract.SCHEMA, compositionId=str(uuid.uuid4()), applicationRole=self.contract.ROLE,
            baseClipGuid='c' * 32, baseClipLocalId=2**60 + 13, baseClipSha256='a' * 64,
            referencePacketFile=str(self.root / 'A.cdanim.json'), referencePacketSha256='b' * 64,
            editWorkItemId=str(uuid.uuid4()), mask='UpperBody', blendMode='Override', weight=1.,
            humanRoles=['LeftUpperArm'], allowedBonePaths=['Hips/Spine/UpperArm.L'], editableControlIds=[],
            sourceRestSha256='c' * 64, timeMode='HoldPose', referenceSpeed=1., editSpeed=1.,
            referencePhase=0., editPhase=0., fps=60., sampleStart=0., sampleEnd=1., weaponLocalMatrix=None,
            weaponPrefabLocalMatrix=IDENTITY[:], weaponContextFile=str(self.root / 'weapon.json'),
            weaponContextSha256='d' * 64, gripTargets=[])
        self.record = {'schema': self.link.SCHEMA, 'linkId': str(uuid.uuid4()), 'targetGuid': '1' * 32,
            'clipGuid': '2' * 32, 'clipLocalId': 2**60 + 31, 'sourcePackage': str(self.packet),
            'sourcePackageSha256': self.link._sha256(self.packet), 'modelFile': str(self.model),
            'modelSha256': self.link._sha256(self.model), 'workItemId': self.spec['editWorkItemId'],
            'editScope': self.contract.SCOPE, 'composition': copy.deepcopy(self.spec)}
        self.path.write_text(json.dumps(self.record), encoding='utf-8')
        self.slot = NS(handle=7, target_id_type='OBJECT')
        self.action = Native(name='Actual B Custom', library=None)
        self.action[self.link.PACKAGE_KEY] = str(self.packet)
        self.action[self.link.PACKAGE_HASH_KEY] = self.record['sourcePackageSha256']
        self.rig = Native(name='CoshaRig', type='ARMATURE', library=None, animation_data=NS(action=self.action, action_slot=self.slot))
        sys.modules['bpy'].data = NS(objects=[self.rig])
        self.package.animation_export.begin_export = lambda *args, **kw: self.fail('An ordinary worker must not export a composition.')

    def load(self, name):
        spec = importlib.util.spec_from_file_location(self.package_name + '.' + name, SOURCE / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        setattr(self.package, name, module)
        return module

    def test_Worklist_association_preserves_full_composition_at_actual_Link_boundary(self):
        item = NS(rig=self.rig, link_identity=json.dumps(self.record), manifest_path=str(self.path),
            custom_slot=7, custom_action=self.action, export_rig_name='CoshaRig',
            model_file=str(self.model), model_sha256=self.record['modelSha256'])
        self.worklist._association(item)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('UI association read a file')), \
             patch.object(Path, 'resolve', side_effect=AssertionError('UI association resolved a path')):
            saved = self.link.get_link(self.rig)
        self.assertEqual(saved['identity']['composition'], self.spec)
        self.assertEqual(saved['identity']['editScope'], self.contract.SCOPE)
        self.assertIs(self.rig[self.link.ACTION_KEY], self.action)
        self.assertEqual(saved['identity']['clipLocalId'], 2**60 + 31)

    def test_generic_bind_preserves_metadata_but_linked_export_rejects_before_lock(self):
        self.link.bind_action(self.rig, self.action, self.path, 'CoshaRig', self.model)
        self.assertEqual(self.link.get_link(self.rig)['identity']['composition'], self.spec)
        with self.assertRaisesRegex(self.link.AnimationLinkError, 'isolated Sync B'):
            self.link.begin_linked_export(NS(), self.rig)
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())

    def test_dropped_composition_does_not_weaken_upper_body_scope_guard(self):
        without = {key: value for key, value in self.record.items() if key != 'composition'}
        with self.assertRaisesRegex(self.link.AnimationLinkError, 'requires its explicit composition'):
            self.link._validate_identity(without)

    def test_unknown_composition_rejects_before_binding_mutation(self):
        changed = copy.deepcopy(self.record); changed['composition']['schema'] = 'unknown/9'
        self.path.write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaises(self.link.AnimationLinkError):
            self.link.bind_action(self.rig, self.action, self.path, 'CoshaRig', self.model)
        self.assertFalse(self.rig)

    def test_A_configuration_is_authenticated_by_actual_Link_identity(self):
        changed = copy.deepcopy(self.record); changed['composition']['referencePhase'] = .375
        self.assertNotEqual(self.link._identity(changed), self.link._identity(self.record))
        identity = self.link._identity(self.record)
        self.assertEqual(json.loads(json.dumps(identity)), json.loads(json.dumps(self.link._identity(self.record))))

    def test_source_only_A_has_no_fake_slot_and_cannot_use_ordinary_export(self):
        source = {key: value for key, value in self.record.items() if key != 'composition'}
        source.update(sourceOnly=True, editScope='All', baseClipGuid='', baseClipLocalId=0,
                      compositionReferenceSource=True, compositionOwnerLinkGuid='a' * 32)
        self.path.write_text(json.dumps(source), encoding='utf-8')
        self.link.bind_action(self.rig, self.action, self.path, 'CoshaRig', self.model)
        saved = self.link.get_link(self.rig)
        self.assertTrue(saved['identity']['sourceOnly'])
        self.assertIn('prepared-source-only', self.link._identity(source))
        with self.assertRaisesRegex(self.link.AnimationLinkError, 'read-only source provenance'):
            self.link.begin_linked_export(NS(), self.rig)
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())

    def test_actual_controller_A_companion_is_also_readonly_and_owner_is_authenticated(self):
        source = {key: value for key, value in self.record.items() if key != 'composition'}
        source.update(sourceOnly=False, editScope='All', baseClipGuid='e' * 32, baseClipLocalId=31,
                      compositionReferenceSource=True, compositionOwnerLinkGuid='a' * 32)
        self.path.write_text(json.dumps(source), encoding='utf-8')
        self.link.bind_action(self.rig, self.action, self.path, 'CoshaRig', self.model)
        saved = self.link.get_link(self.rig)['identity']
        self.assertEqual(saved['compositionOwnerLinkGuid'], 'a' * 32)
        self.assertTrue(saved['compositionReferenceSource'])
        with self.assertRaisesRegex(self.link.AnimationLinkError, 'A Source Link remains read-only'):
            self.link.begin_linked_export(NS(), self.rig)
        self.assertFalse(Path(str(self.path) + '.blender.lock').exists())
        changed = {**source, 'compositionOwnerLinkGuid': 'b' * 32}
        self.assertNotEqual(self.link._identity(changed), self.link._identity(source))

    def test_companion_reference_flags_and_owner_are_strict(self):
        source = {key: value for key, value in self.record.items() if key != 'composition'}
        source.update(editScope='All', compositionReferenceSource=True, compositionOwnerLinkGuid='a' * 32)
        for fields in ({'compositionReferenceSource': 1}, {'compositionOwnerLinkGuid': ''},
                       {'compositionReferenceSource': False}, {'editScope': self.contract.SCOPE}, {'workItemId': ''}):
            with self.subTest(fields=fields), self.assertRaises(self.link.AnimationLinkError):
                self.link._validate_identity({**source, **fields})

    def test_source_only_wrong_type_invented_slot_or_upperbody_reject(self):
        source = {key: value for key, value in self.record.items() if key != 'composition'}
        source.update(sourceOnly=True, editScope='All', baseClipGuid='', baseClipLocalId=0)
        for fields in ({'sourceOnly': 1}, {'baseClipGuid': 'f' * 32, 'baseClipLocalId': 5},
                       {'editScope': self.contract.SCOPE}, {'workItemId': ''}):
            with self.subTest(fields=fields), self.assertRaises(self.link.AnimationLinkError):
                self.link._validate_identity({**source, **fields})

    def test_legacy_route_is_exact_when_source_only_is_absent_or_false(self):
        ordinary = {key: value for key, value in self.record.items() if key != 'composition'}
        ordinary['editScope'] = 'All'
        self.assertEqual(self.link._identity(ordinary), self.link._identity({**ordinary, 'sourceOnly': False}))


if __name__ == '__main__':
    unittest.main()
