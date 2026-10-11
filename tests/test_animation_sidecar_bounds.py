"""Complete bounded animation sidecars and durable receiver failures, no Blender."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

import test_animation_worklist_collection as collection_fixtures
import test_animation_worklist_new as new_fixtures
import test_character_composition_sync_fixture as sync_fixtures

REPO = Path(__file__).resolve().parents[1]


class SidecarBoundsTests(unittest.TestCase):
    def setUp(self):
        self.collect = collection_fixtures.CollectionTests(methodName='runTest')
        self.collect.setUp()
        self.addCleanup(self.collect.doCleanups)
        self.create = new_fixtures.NewActionTests(methodName='runTest')
        self.create.setUp()
        self.addCleanup(self.create.doCleanups)
        self.links = sys.modules[self.create.new.__package__].animation_link
        self.collect.links.ANIMATION_METADATA_MAX_BYTES = self.links.ANIMATION_METADATA_MAX_BYTES
        self.contract = self.collect.load(self.collect.collection.__package__, 'animation_composition_contract')
        self.spec = self.make_spec()
        self.meta = self.metadata(self.spec)
        self.proof, self.values = self.numeric_proof(self.spec)
        self.item = NS(work_item_id=self.spec['editWorkItemId'], new_action_id='', new_animation=False)

    def make_spec(self, *, new_id=''):
        spec = {key: '' for key in self.contract.FIELDS}
        spec.update(schema=self.contract.SCHEMA, compositionId=str(uuid.UUID('1'*32)),
            applicationRole=self.contract.ROLE, baseClipGuid='2'*32, baseClipLocalId=74,
            baseClipSha256='a'*64, referencePacketFile='D:/Reference.cdanim.json', referencePacketSha256='b'*64,
            editWorkItemId=self.create.item_id if new_id else str(uuid.UUID('3'*32)), editNewActionId=new_id,
            mask='UpperBody', blendMode='Override', weight=1, humanRoles=['Spine'],
            allowedBonePaths=['CoshaRig/Hips/upper'+str(index) for index in range(41)], editableControlIds=[],
            sourceRestSha256='c'*64, timeMode='HoldLoop', referenceSpeed=.8, editSpeed=1.25,
            referencePhase=.07, editPhase=.19, fps=60, sampleStart=0, sampleEnd=297/60,
            holdWorkItemId='',holdNewActionId='',weaponLocalMatrix=None,
            weaponPrefabLocalMatrix=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],
            weaponContextFile='D:/Weapon.json',weaponContextSha256='d'*64,gripTargets=[])
        return self.contract.validate(spec)

    def metadata(self, spec):
        return dict(composition=spec,spec_sha256=self.contract.identity(spec),
            reference_action_sha256='e'*64,reference_packet_sha256=spec['referencePacketSha256'])

    def numeric_proof(self, spec):
        # Execute only this existing pure numeric helper, never the exporter module.
        path=REPO/'addons/character_designer/animation_composition_export.py'
        node=next(node for node in ast.parse(path.read_text(encoding='utf-8')).body
                  if isinstance(node,ast.FunctionDef) and node.name=='effective_values_proof')
        def require(ok,message):
            if not ok: raise ValueError(message)
        namespace=dict(_require=require,math=math,struct=struct,contract=self.contract,
            VALUES_SCHEMA='cdesigner.composition-effective-values/1')
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
        names=sorted(path+'|'+prop+'|'+str(component) for path in spec['allowedBonePaths']
            for prop,count in (('localPosition',3),('localRotation',4),('localScale',3))
            for component in range(count))
        times=[frame/60 for frame in range(298)]
        measured=namespace['effective_values_proof'](names,times,
            {name:[math.sin(index*.1+frame*.013)*.2 for frame in range(298)] for index,name in enumerate(names)})
        proof=dict(schema=self.contract.EXPORT_SCHEMA,compositionId=spec['compositionId'],
            editWorkItemId=spec['editWorkItemId'],editNewActionId=spec['editNewActionId'],sourceAction='NewC',
            sourceActionSlot=7,isolatedEditAction='Owned B snapshot',referenceActionUnchanged=True,
            referenceActionBeforeSha256='e'*64,referenceActionAfterSha256='e'*64,
            effectiveChannelsSha256=measured['effectiveChannelsSha256'],effectiveChannelCount=410,
            effectiveChannels=names,sampleSpace='bone-local',excludedReferenceEvaluation=True)
        return proof,measured['effectiveValues']

    def payload(self,spec=None,proof=None):
        return dict(composition=spec or self.spec,composition_export=proof or self.proof,
            effective_values=self.values,action='NewC',channels='skeletal-only',duration=297/60)

    def write_source(self,payload=None):
        path=self.collect.metadata
        path.write_text(json.dumps(payload or self.payload(),allow_nan=False),encoding='utf-8')
        return dict(metadataFile=str(path),metadataSha256=self.collect.digest(path),composition=self.spec)

    def source_publish(self,link):
        return self.collect.collection._published_composition(self.item,link,self.meta,self.proof,current=True)

    def new_context(self):
        spec=self.make_spec(new_id=self.create.action_id)
        meta=self.metadata(spec)
        proof={**self.proof,**{key:spec[key] for key in ('compositionId','editWorkItemId','editNewActionId')}}
        captured={**self.create.record,'editScope':self.contract.SCOPE,'composition':spec}
        self.create.new._write(self.create.manifest,captured,expected=self.create.record)
        result=self.create.result()
        output=self.payload(spec,proof)
        output['fbx_sha256']=result['fbx_sha256']
        Path(result['metadata']).write_text(json.dumps(output,allow_nan=False),encoding='utf-8')
        return captured,result,meta,proof

    def publish_new(self,captured,result,meta,proof):
        key=self.create.new.__package__+'.animation_worklist_collection'
        with patch.dict(sys.modules,{key:self.collect.collection}):
            return self.create.new.publish(self.create.manifest,captured,result,
                blend_file='',source_action='NewC',composition_metadata=meta,composition_export=proof)

    def oversized_stat(self,metadata):
        original=Path.stat
        def changed(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if path == metadata:
                return NS(st_mode=value.st_mode,st_size=self.links.ANIMATION_METADATA_MAX_BYTES+1)
            return value
        return changed

    def test_source_full_410_by_298_numeric_payload_above_1mib_is_accepted(self):
        link=self.write_source()
        self.assertGreater(self.collect.metadata.stat().st_size,1024*1024)
        self.assertEqual(len(self.values['channels']),410)
        self.assertTrue(all(len(row[1])==298 for row in self.values['channels']))
        self.assertEqual(self.source_publish(link),(self.meta,self.proof))

    def test_new_full_payload_above_1mib_publishes_actual_manifest_revision(self):
        captured,result,meta,proof=self.new_context()
        self.assertGreater(Path(result['metadata']).stat().st_size,1024*1024)
        returned=self.publish_new(captured,result,meta,proof)
        record=self.create.new.load(self.create.manifest)
        self.assertEqual(returned['revision'],1)
        self.assertEqual(record['metadataSha256'],self.links._sha256(Path(result['metadata'])))
        self.assertEqual(record['composition'],captured['composition'])

    def test_both_sidecar_readers_reject_above_32mib_before_json_parse(self):
        link=self.write_source()
        with patch.object(Path,'stat',new=self.oversized_stat(self.collect.metadata)), \
                patch.object(json,'loads',side_effect=AssertionError('No oversized parsing')):
            with self.assertRaisesRegex(ValueError,'exceeds 32 MiB'):
                self.source_publish(link)
        captured,result,meta,proof=self.new_context()
        before=self.create.manifest.read_bytes()
        with patch.object(Path,'stat',new=self.oversized_stat(Path(result['metadata']))), \
                patch.object(json,'loads',side_effect=AssertionError('No oversized parsing')):
            with self.assertRaisesRegex(ValueError,'exceeds 32 MiB'):
                self.publish_new(captured,result,meta,proof)
        self.assertEqual(self.create.manifest.read_bytes(),before)

    def test_source_sha_tamper_rejects_before_json_parse(self):
        link=self.write_source()
        self.collect.metadata.write_bytes(self.collect.metadata.read_bytes()+b' ')
        with patch.object(json,'loads',side_effect=AssertionError('No changed-file parsing')):
            with self.assertRaisesRegex(ValueError,'changed after publication'):
                self.source_publish(link)

    def test_source_sealed_dto_or_isolation_proof_tamper_rejects_even_with_new_sha(self):
        for field in ('composition','composition_export'):
            with self.subTest(field=field):
                payload=self.payload()
                payload[field]=deepcopy(payload[field])
                if field=='composition':payload[field]['editPhase']=.3
                else:payload[field]['sourceActionSlot']=8
                with self.assertRaisesRegex(ValueError,'frozen isolated B proof'):
                    self.source_publish(self.write_source(payload))

    def test_expected_channel_action_slot_or_reference_proof_tamper_rejects(self):
        link=self.write_source()
        for field,value in (('sourceActionSlot',-1),('referenceActionUnchanged',False),
                            ('effectiveChannels',self.proof['effectiveChannels'][:-1])):
            with self.subTest(field=field):
                bad={**self.proof,field:value}
                with self.assertRaises(ValueError):
                    self.collect.collection._published_composition(self.item,link,self.meta,bad)

    def test_new_fbx_dto_or_export_proof_tamper_retains_revision_zero(self):
        captured,result,meta,proof=self.new_context()
        original=Path(result['metadata']).read_bytes(); before=self.create.manifest.read_bytes()
        for tamper in ('fbx_sha256','composition','composition_export'):
            with self.subTest(tamper=tamper):
                payload=json.loads(original)
                if tamper=='fbx_sha256':payload[tamper]='f'*64
                elif tamper=='composition':payload[tamper]['editSpeed']=2
                else:payload[tamper]['sourceActionSlot']=8
                Path(result['metadata']).write_text(json.dumps(payload),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'metadata'):
                    self.publish_new(captured,result,meta,proof)
                self.assertEqual(self.create.manifest.read_bytes(),before)

    def test_shared_sidecar_bound_does_not_enlarge_link_or_new_manifest_1mib(self):
        self.assertEqual(self.links.ANIMATION_METADATA_MAX_BYTES,32*1024*1024)
        for path,reader in ((self.collect.manifest,self.links.load_link),
                            (self.create.manifest,self.create.new.load)):
            path.write_text('{}'+' '*(1024*1024),encoding='utf-8')
            with patch.object(json,'loads',side_effect=AssertionError('No oversized manifest parsing')):
                with self.assertRaisesRegex(ValueError,'1 MiB|too large'):
                    reader(path)


class SyncReceiptErrorTests(unittest.TestCase):
    def run_failure(self,directory,result,error):
        sync=sync_fixtures.sync
        row=sync_fixtures.config(directory)['items'][0]; measured=sync_fixtures.numeric(row)
        measured['composition_export']['effectiveChannelsSha256']='f'*64
        item=NS(rig=object(),manifest_path=row['manifestPath'],new_animation=False)
        saved=NS(rig=item.rig,active_index=4)
        context=object()
        job=dict(root=Path(directory)/'worker_root',process=Mock(poll=Mock(return_value=0)),started=0.)
        job['root'].mkdir(); (job['root']/'log.txt').write_text('worker completed')
        exporter=NS(active_job=lambda:None)
        composition=NS(active=lambda rig:False,begin_sync=Mock(return_value=job),_poll_sync=lambda:None)
        export=NS(poll_isolated_export=Mock(return_value=result),cancel_isolated_export=Mock())
        worklist=NS(state=lambda ctx:saved,_capture_rig=Mock(return_value={'native':'saved'}),_restore_rig=Mock())
        source=NS(_context_state=Mock(return_value={}),_restore_context=Mock())
        collector=NS(export_finished=Mock(return_value=error))
        modules=dict(animation_composition=composition,animation_composition_export=export,
            animation_export=exporter,animation_worklist=worklist,animation_worklist_collection=collector,
            animation_link=NS(),animation_worklist_new=NS(),animation_link_source=source,
            animation_worklist_fingerprint=NS())
        bpy=ModuleType('bpy'); bpy.app=NS(timers=NS(is_registered=lambda timer:False))
        native=ModuleType('verify_character_composition_native'); native.action_proofs=Mock(return_value={})
        with patch.dict(sys.modules,{'bpy':bpy,native.__name__:native}), \
                patch.object(sync,'_services',return_value=modules), \
                patch.object(sync,'_native_row',return_value=item), \
                patch.object(sync,'foreground_one',return_value=measured):
            with self.assertRaises(AssertionError) as caught:
                sync.run_one(context,row,str(Path(directory)/'evidence'),measured)
        report=json.loads((Path(directory)/'evidence/composition_sync_report.json').read_text(encoding='utf-8'))
        worklist._restore_rig.assert_called_once()
        source._restore_context.assert_called_once()
        return report,str(caught.exception),collector.export_finished

    def test_exact_collector_error_is_persisted_not_replaced_by_umbrella(self):
        with sync_fixtures.tempfile.TemporaryDirectory() as directory:
            error='The mandatory isolated B proof has a changed Action slot.'
            report,message,collector=self.run_failure(directory,{'real':'published'},error)
            self.assertEqual(message,error)
            self.assertEqual(report['error'],error)
            self.assertEqual(report['collection_receipt_error'],error)
            self.assertFalse(report['completed'])
            self.assertTrue(report['worker_launch_performed'])
            collector.assert_called_once()

    def test_missing_export_result_remains_distinct_and_does_not_call_collector(self):
        with sync_fixtures.tempfile.TemporaryDirectory() as directory:
            report,message,collector=self.run_failure(directory,None,'must not be called')
            self.assertIn('returned no publication result',message)
            self.assertNotIn('collection_receipt_error',report)
            self.assertFalse(report['completed'])
            collector.assert_not_called()


if __name__=='__main__':
    unittest.main()
