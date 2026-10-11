"""Canonical A-origin and sealed new-B ownership rules, native boundaries mocked."""
from copy import deepcopy
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


class Action(dict):
    def __init__(self, name, library=None):
        super().__init__()
        self.name, self.library, self.use_fake_user = name, library, False
        self.frame_range, self.slots = (1., 61.), [NS(handle=7, target_id_type='OBJECT')]


class Object(dict):
    def __bool__(self):
        return True


class Items(list):
    def add(self):
        row = NS(source_action=None, source_file='', source_hash='')
        self.append(row)
        return row

    def remove(self, index):
        del self[index]


class NativeTemplateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cd-native-template-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.package = '_cd_templates_' + uuid.uuid4().hex
        package = ModuleType(self.package)
        package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        bpy.types, bpy.path = NS(Action=Action, Object=Object), NS(abspath=lambda value: value)
        modules = patch.dict(sys.modules, {self.package: package, 'bpy': bpy})
        modules.start(); self.addCleanup(modules.stop)
        for short in ('animation_workspace', 'animation_link', 'animation_composition_contract',
                      'animation_composition_controls', 'animation_worklist_new'):
            self.load(short)
        self.workspace = package.animation_workspace
        self.links, self.contract, self.controls, self.new = (package.animation_link,
            package.animation_composition_contract, package.animation_composition_controls,
            package.animation_worklist_new)
        self.model, self.packet_file, self.source_file = (self.root / name for name in ('Model.fbx', 'Source.cdanim.json', 'Source.blend'))
        self.model.write_bytes(b'current model')
        self.packet_file.write_text('{}', encoding='utf-8')
        self.source_file.write_bytes(b'protected native Source cache')
        self.manifest, self.workspace_file = self.root / self.links.MANIFEST_NAME, self.root / self.workspace.MANIFEST_NAME
        self.workspace_file.write_text('{}', encoding='utf-8')
        self.catalog = dict(schema=self.workspace.SCHEMA, workspaceId=uuid.uuid4().hex,
            projectId=uuid.uuid4().hex, targetGuid='a' * 32, targetHash='b' * 32,
            baseControllerGuid='c' * 32, avatarGuid='d' * 32, avatarHash='e' * 32,
            modelFile=str(self.model), modelSha256=self.links._sha256(self.model),
            workspaceTemplateLinkGuid='f' * 32, targetName='Cosha', _manifest_path=str(self.workspace_file))
        self.link = dict(schema=self.links.SCHEMA, linkId=uuid.uuid4().hex, targetGuid='a' * 32,
            clipGuid='1' * 32, clipLocalId=74, workItemId=uuid.uuid4().hex,
            sourcePackage=str(self.packet_file), sourcePackageSha256=self.links._sha256(self.packet_file),
            modelFile=str(self.model), modelSha256=self.catalog['modelSha256'], editScope='All', revision=0,
            sourceOnly=True,compositionReferenceSource=True,compositionOwnerLinkGuid='7'*32)
        self.packet = dict(_sha256=self.links._sha256(self.packet_file), targetGuid='a' * 32,
            clipGuid='1' * 32, clipLocalId=74, bones=[dict(parent=-1, name='CoshaRig', path='CoshaRig', restSource='hierarchy', weighted=False)])
        self.source = Action('Protected Source', NS(filepath=str(self.source_file)))
        self.custom, self.candidate = Action('Template Custom'), Action('Separate Work Copy')
        self.rig = Object()
        self.rig.name, self.rig.type, self.rig.library = 'CoshaRig', 'ARMATURE', None
        self.rig.animation_data = NS(action=self.candidate, action_slot=self.candidate.slots[0])
        self.saved = NS(rig=self.rig, items=Items(), active_index=-1,
            workspace_path='D:/Historical.workspace.json', workspace_identity='historical')
        self.context = NS(scene=NS(objects={self.rig.name:self.rig}, render=NS(fps=60, fps_base=1)))
        self.worklist = self.service('animation_worklist')
        self.worklist._encoded = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        self.worklist._identity = lambda value: self.worklist._encoded([self.workspace.workspace_identity(value),
            {field:value[field] for field in ('projectId','baseControllerGuid','avatarGuid','avatarHash')}])
        self.worklist.state, self.worklist._idle = lambda _context:self.saved, lambda _context:None
        self.worklist._clip_key = self.workspace.row_key
        self.worklist._slot = self.slot
        self.worklist._check_baseline = self.baseline
        self.worklist._prepared = self.prepared
        self.worklist._fresh_workspace = lambda _saved, _row=None: deepcopy(self.catalog)
        self.workspace.load_workspace = Mock(side_effect=lambda _path: deepcopy(self.catalog))
        self.composition = self.service('animation_composition')
        self.composition.active = lambda _rig:False
        self.composition.item_spec = lambda row: self.contract.validate(json.loads(row.composition_json)) if getattr(row,'composition_json','') else None
        self.composition._library_state = lambda _context:(deepcopy(self.catalog), NS(workspace_path=str(self.workspace_file), workspace_identity=self.worklist._identity(self.catalog)))
        self.composition.bind_item = self.bind_item
        self.composition._packet_for_item = lambda row:self.current_packet(row.custom_action)
        self.ua = self.service('unity_animation')
        self.ua.load_package = lambda _path:deepcopy(self.packet)
        self.target = self.service('animation_worklist_target')
        self.target._action_motion_keys = lambda:('motion_rig','original_data','preview_data')
        self.target.attach_action_motion_data = lambda rig,action:action.update(motion_rig=rig, original_data='artist', preview_data='owned')
        self.export = self.service('animation_composition_export')
        self.export.resolve_scope = self.resolve
        self.export.effective_fingerprint = lambda *_args:{'fingerprint_known':True}
        self.publish_template()

    def load(self, short):
        name = self.package + '.' + short
        spec = importlib.util.spec_from_file_location(name, SOURCE / (short + '.py'))
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None); spec.loader.exec_module(module)
        setattr(sys.modules[self.package],short,module)
        return module

    def service(self, short):
        module = ModuleType(self.package + '.' + short)
        sys.modules[module.__name__] = module
        self.addCleanup(sys.modules.pop,module.__name__,None)
        setattr(sys.modules[self.package],short,module)
        return module

    def slot(self, action, handle=None):
        slots = [slot for slot in action.slots if slot.target_id_type == 'OBJECT' and (handle is None or slot.handle == handle)]
        if len(slots) != 1: raise ValueError('ambiguous native slot')
        return slots[0]

    def baseline(self, row):
        if (row.source_action.library is None or Path(row.source_action.library.filepath).resolve() != Path(row.source_file).resolve()
                or self.links._sha256(Path(row.source_file)) != row.source_hash):
            raise ValueError('protected Source changed')

    def prepared(self, route, key):
        clip = next((row for row in self.catalog['clips'] if self.workspace.row_key(row) == key),None)
        link = self.links.load_link(clip['linkManifest']) if clip else None
        packet = self.prepared_packets.get(link['sourcePackage']) if link else None
        if (link is None or clip.get('needsRebuild') or self.workspace.route_identity(clip) != self.workspace.route_identity(link)
                or self.links._sha256(self.model) != self.catalog['modelSha256']
                or link['modelSha256'] != self.catalog['modelSha256']
                or any(packet[field] != link[field] for field in ('targetGuid','clipGuid','clipLocalId'))):
            raise ValueError('actual prepared Link/source/model changed')
        return deepcopy(self.catalog),deepcopy(clip),link,deepcopy(packet),'CoshaRig',self.model

    def current_packet(self, action):
        if (action.get(self.links.PACKAGE_KEY) != str(self.packet_file)
                or action.get(self.links.PACKAGE_HASH_KEY) != self.links._sha256(self.packet_file)):
            raise ValueError('exact native packet changed')
        return deepcopy(self.packet)

    def publish_template(self):
        self.manifest.write_text(json.dumps(self.link),encoding='utf-8')
        clip = {**deepcopy(self.link),'linkManifest':str(self.manifest),'linkGuid':'8'*32,'needsRebuild':False,'clipName':'Actual Source'}
        self.catalog['clips'] = [clip]
        self.prepared_packets = {str(self.packet_file):self.packet}
        if self.link.get('compositionReferenceSource'):
            owner_folder = self.root/'OwnerB'; owner_folder.mkdir(exist_ok=True)
            owner_packet_file = owner_folder/'B.cdanim.json'; owner_packet_file.write_text('{}',encoding='utf-8')
            owner_link = {**self.link,'linkId':'9'*32,'clipGuid':'0'*32,'clipLocalId':123,'workItemId':'2'*32,
                'sourcePackage':str(owner_packet_file),'sourcePackageSha256':self.links._sha256(owner_packet_file),
                'editScope':self.contract.SCOPE,'sourceOnly':False,'compositionReferenceSource':False,'compositionOwnerLinkGuid':''}
            owner_spec = self.composition_spec()
            owner_spec.update(editWorkItemId=owner_link['workItemId'],baseClipGuid=self.link['clipGuid'],
                baseClipLocalId=self.link['clipLocalId'],referencePacketFile=str(self.packet_file),referencePacketSha256=self.packet['_sha256'])
            owner_link['composition'] = self.contract.validate(owner_spec)
            owner_manifest = owner_folder/self.links.MANIFEST_NAME
            owner_manifest.write_text(json.dumps(owner_link),encoding='utf-8')
            self.catalog['clips'].append({**deepcopy(owner_link),'linkGuid':'7'*32,'linkManifest':str(owner_manifest),'needsRebuild':False})
            self.prepared_packets[str(owner_packet_file)] = {**self.packet,'_sha256':owner_link['sourcePackageSha256'],
                'clipGuid':owner_link['clipGuid'],'clipLocalId':owner_link['clipLocalId'],'composition':owner_link['composition']}
        self.source[self.links.PACKAGE_KEY], self.source[self.links.PACKAGE_HASH_KEY] = str(self.packet_file), self.packet['_sha256']
        self.template_row = NS(item_id=self.link['workItemId'], work_item_id=self.link['workItemId'], rig=self.rig,
            new_animation=False, custom_action=self.custom, custom_slot=7, source_action=self.source, source_slot=7,
            source_file=str(self.source_file), source_hash=self.links._sha256(self.source_file),
            workspace_path=str(self.workspace_file),workspace_identity=self.worklist._identity(self.catalog),
            clip_key=self.workspace.row_key(clip),manifest_path=str(self.manifest),link_identity=json.dumps(self.link),
            composition_json=json.dumps(self.link['composition']) if self.link.get('composition') else '')
        self.saved.items[:] = [self.template_row]

    def origin(self):
        return dict(workspace_manifest=str(self.workspace_file),workspace_identity=self.worklist._identity(self.catalog),
            clip_key=self.template_row.clip_key,link_manifest=str(self.manifest),source_packet=str(self.packet_file),source_packet_sha256=self.packet['_sha256'])

    def composition_spec(self):
        value = {field:'' for field in self.contract.FIELDS}
        value.update(schema=self.contract.SCHEMA,compositionId='2'*32,applicationRole=self.contract.ROLE,
            baseClipGuid='3'*32,baseClipLocalId=74,baseClipSha256='a'*64,referencePacketFile=str(self.root/'A.json'),
            referencePacketSha256='b'*64,editWorkItemId=self.link['workItemId'],editNewActionId='',mask='UpperBody',
            blendMode='Override',weight=1,humanRoles=['Spine'],allowedBonePaths=['CoshaRig/Hips/spine'],editableControlIds=[],
            sourceRestSha256='c'*64,timeMode='HoldLoop',referenceSpeed=1.,editSpeed=1.,referencePhase=0.,editPhase=0.,
            fps=60.,sampleStart=0.,sampleEnd=1.,holdWorkItemId='',holdNewActionId='',weaponLocalMatrix=None,gripTargets=[],
            weaponContextFile=str(self.root/'weapon.json'),weaponContextSha256='d'*64)
        for field in self.contract.FIELDS:
            if field.endswith('Matrix') and field != 'weaponLocalMatrix': value[field] = [1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]
        return self.contract.validate(value)

    def b(self):
        sealed = self.composition_spec()
        self.packet['composition'], self.link['composition'], self.link['editScope'] = sealed,sealed,self.contract.SCOPE
        self.link['sourceOnly'], self.link['compositionReferenceSource'] = False,False
        self.link['compositionOwnerLinkGuid'] = ''
        self.publish_template()
        return self.contract.validate({**sealed,'compositionId':'4'*32,'editWorkItemId':'5'*32,'editNewActionId':'6'*32})

    def bind_item(self,row,record,**route):
        row.composition_json, row.composition_sha256 = json.dumps(record['composition']), self.contract.identity(record['composition'])
        row.edit_scope, row.application_role = self.contract.SCOPE,self.contract.ROLE
        row.workspace_path,row.workspace_identity = route['workspace_path'],route['workspace_identity']

    def resolve(self,context,rig,spec,packet,*,new_creation=None):
        self.controls.validate_packet_composition(spec,packet)
        self.new.authenticate_composition_bind_template(context,rig,spec,packet,creation=new_creation)
        return NS(channels=())

    def test_a_copy_has_fresh_ids_actual_template_packet_and_no_source_or_b(self):
        before = (self.manifest.read_bytes(),self.packet_file.read_bytes(),self.source_file.read_bytes())
        row = self.new.register(self.context,origin_template=self.origin())
        record = self.new.load(row.manifest_path)
        self.assertNotEqual(row.work_item_id,self.link['workItemId'])
        self.assertEqual(record['workspaceTemplateLinkGuid'],uuid.UUID(self.link['linkId']).hex)
        self.assertEqual((row.edit_scope,row.composition_json,row.application_role),('All','',''))
        self.assertIsNone(row.source_action); self.assertEqual((row.source_file,row.source_hash),('',''))
        self.assertIs(self.candidate['motion_rig'],self.rig)
        self.assertEqual(self.candidate[self.links.PACKAGE_HASH_KEY],self.packet['_sha256'])
        self.assertEqual(before,(self.manifest.read_bytes(),self.packet_file.read_bytes(),self.source_file.read_bytes()))
        self.assertEqual(self.new.validate(self.saved,row,self.catalog),record)
        self.assertEqual(self.saved.workspace_identity,'historical')

    def test_a_origin_source_guid_packet_model_and_b_scope_are_not_interchangeable(self):
        for change in ('packet','guid','model','b','finger-packet'):
            with self.subTest(change=change):
                original = deepcopy(self.link),deepcopy(self.packet),deepcopy(self.catalog)
                if change == 'packet': template = {**self.origin(),'source_packet_sha256':'0'*64}
                else: template = self.origin()
                if change == 'guid': self.packet['clipGuid'] = '0'*32
                if change == 'model': self.catalog['modelSha256'] = '0'*64
                if change == 'b': self.link['editScope'] = 'UpperBodyOverride'
                if change == 'finger-packet': self.packet['editScope'] = 'FingersLeft'
                self.manifest.write_text(json.dumps(self.link),encoding='utf-8')
                with self.assertRaises(ValueError): self.new.register(self.context,origin_template=template)
                self.link,self.packet,self.catalog = original
                self.publish_template()
        self.assertEqual(dict(self.candidate),{})

    def test_a_proof_deletion_or_packet_override_blocks_even_if_global_template_matches(self):
        row = self.new.register(self.context,origin_template=self.origin())
        self.catalog['workspaceTemplateLinkGuid'] = uuid.UUID(self.link['linkId']).hex
        receipt = self.candidate.pop(self.new.ORIGIN_TEMPLATE_KEY)
        with self.assertRaisesRegex(ValueError,'origin template'): self.new.validate(self.saved,row,self.catalog)
        self.candidate[self.new.ORIGIN_TEMPLATE_KEY] = receipt
        self.candidate[self.links.PACKAGE_HASH_KEY] = '0'*64
        with self.assertRaisesRegex(ValueError,'origin Link'): self.new.validate(self.saved,row,self.catalog)

    def test_a_failed_row_commit_restores_native_refs_and_creation_files(self):
        self.candidate['original_data'] = 'previous artist ref'
        self.saved.items.add = Mock(side_effect=ValueError('native row failure'))
        before = dict(self.candidate)
        with self.assertRaisesRegex(ValueError,'native row'): self.new.register(self.context,origin_template=self.origin())
        self.assertEqual(dict(self.candidate),before)
        self.assertFalse(self.candidate.use_fake_user)
        self.assertEqual(list((self.root/'new_actions').glob('*/character_animation_new.json')),[])

    def test_a_companion_owner_and_readonly_flag_must_match_current_b(self):
        template = self.origin()
        owner = self.catalog['clips'][1]
        owner['composition']['referencePacketSha256'] = '0'*64
        with self.assertRaisesRegex(ValueError,'owner|changed'): self.new.register(self.context,origin_template=template)
        self.publish_template()
        self.link['compositionReferenceSource'] = False
        self.link['compositionOwnerLinkGuid'] = ''
        self.manifest.write_text(json.dumps(self.link),encoding='utf-8')
        self.catalog['clips'][0].update(compositionReferenceSource=False,compositionOwnerLinkGuid='')
        with self.assertRaisesRegex(ValueError,'readonly companion'): self.new.register(self.context,origin_template=template)

    def test_a_origin_alias_to_b_or_existing_source_action_never_creates_new_row(self):
        self.rig.animation_data.action = self.source
        with self.assertRaises(ValueError): self.new.register(self.context,origin_template=self.origin())
        self.assertEqual(len(self.saved.items),1)
        with self.assertRaisesRegex(ValueError,'also claim B'): self.new.register(self.context,origin_template=self.origin(),composition={})

    def test_native_b_transient_proof_accepts_only_owned_candidate_action_and_slot(self):
        spec = self.b()
        proof = self.new._composition_creation(self.context,self.rig,self.candidate,7,spec,self.packet)
        self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet,creation=proof)
        with self.assertRaisesRegex(ValueError,'transient'): self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet,creation={'receipt':proof.receipt})
        self.rig.animation_data.action_slot = NS(handle=99)
        with self.assertRaisesRegex(ValueError,'transient'): self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet,creation=proof)

    def test_native_b_plain_new_ids_without_row_are_rejected(self):
        spec = self.b()
        with self.assertRaisesRegex(ValueError,'persisted native'): self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet)

    def test_native_b_persisted_registration_reauthenticates_action_manifest_and_template(self):
        spec = self.b()
        row = self.new.register(self.context,composition=spec,packet_path=str(self.packet_file))
        self.assertEqual(self.new.load(row.manifest_path)['workspaceTemplateLinkGuid'],uuid.UUID(self.link['linkId']).hex)
        self.assertNotEqual(self.new.load(row.manifest_path)['workspaceTemplateLinkGuid'],self.catalog['workspaceTemplateLinkGuid'])
        self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet)
        self.assertIs(self.candidate[self.new.BIND_TEMPLATE_KEY]['source_action'],self.source)
        self.candidate[self.new.BIND_TEMPLATE_KEY]['source_action'] = Action('Artist replacement')
        with self.assertRaisesRegex(ValueError,'native per-action template'): self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet)
        self.assertEqual(self.new.load(row.manifest_path)['revision'],0)

    def test_native_b_registered_manifest_preserves_exact_dto_uuid_strings(self):
        spec = self.contract.validate(self.b())
        row = self.new.register(self.context,composition=spec,packet_path=str(self.packet_file))
        record = json.loads(Path(row.manifest_path).read_text(encoding='utf-8'))
        self.assertEqual(record['newActionId'],spec['editNewActionId'])
        self.assertEqual(record['workItemId'],spec['editWorkItemId'])
        self.assertEqual(record['newActionId'],record['composition']['editNewActionId'])
        self.assertEqual(record['workItemId'],record['composition']['editWorkItemId'])
        self.assertEqual(Path(row.manifest_path).parent.name,uuid.UUID(spec['editNewActionId']).hex)
        self.assertEqual(row.work_item_id,uuid.UUID(spec['editWorkItemId']).hex)
        self.assertEqual(row.new_action_id,uuid.UUID(spec['editNewActionId']).hex)

    def test_native_b_sealed_a_change_and_source_cache_change_are_rejected(self):
        spec = self.b()
        proof = self.new._composition_creation(self.context,self.rig,self.candidate,7,spec,self.packet)
        changed = {**spec,'referencePhase':.25}
        with self.assertRaises(ValueError): self.new.authenticate_composition_bind_template(self.context,self.rig,changed,self.packet,creation=proof)
        self.source_file.write_bytes(b'edited readonly native Source')
        with self.assertRaisesRegex(ValueError,'protected Source'): self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet,creation=proof)

    def test_native_b_duplicate_template_or_modified_exact_source_slot_rejects(self):
        spec = self.b()
        self.saved.items.append(NS(**vars(self.template_row)))
        with self.assertRaisesRegex(ValueError,'one actual'): self.new._composition_creation(self.context,self.rig,self.candidate,7,spec,self.packet)
        self.saved.items.pop()
        proof = self.new._composition_creation(self.context,self.rig,self.candidate,7,spec,self.packet)
        self.source.slots[0].handle = 88
        with self.assertRaisesRegex(ValueError,'slot'): self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet,creation=proof)

    def test_native_b_persisted_manifest_and_foreign_rig_receipt_cannot_replace_ownership(self):
        spec = self.b()
        row = self.new.register(self.context,composition=spec,packet_path=str(self.packet_file))
        receipt = self.candidate[self.new.BIND_TEMPLATE_KEY]
        receipt['rig'] = Object()
        with self.assertRaisesRegex(ValueError,'native per-action template'): self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet)
        receipt['rig'] = self.rig
        record = self.new.load(row.manifest_path)
        record['composition']['referencePhase'] = .25
        Path(row.manifest_path).write_text(json.dumps(record),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'configuration'): self.new.authenticate_composition_bind_template(self.context,self.rig,spec,self.packet)


if __name__ == '__main__':
    unittest.main()
