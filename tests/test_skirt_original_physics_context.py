"""Original/Wiggle context authority, disjoint writer and mode-hook checks."""
import ast
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]/'addons/character_designer'


class Object(dict):
    def __init__(self, name, kind='ARMATURE'):
        super().__init__()
        self.name, self.type = name, kind
        self.library = self.override_library = None
        self.is_editable = True
        self.data = NS(bones={})

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


def functions(filename, names, namespace):
    tree=ast.parse((ROOT/filename).read_bytes())
    nodes=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes}==set(names)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/filename),'exec'),namespace)


class OriginalContextTests(unittest.TestCase):
    def setUp(self):
        self.skirt=NS(RECORD_KEY='record', RIG_KEY='rig', OWNER_KEY='owner', SOURCE_KEY='source',
                      _ORIGINAL_SESSION_KEY='original', ORIGINAL_DISPLAY_OWNER_KEY='display_owner',
                      SHARED_SOURCES_KEY='shared')
        self.source=Object('Dress','MESH')
        self.rig=Object('Rig')
        self.rig.data={'owner':'owned'}
        self.source['rig']=self.rig
        self.record={'owner':'owned','chains':[{'phys':['Physics'], 'def':['Deform']}],
                     'controls':{'waist':'Waist'}, 'shared':{'anchor':'Hips'}}
        self.source['record']=json.dumps(self.record)
        self.rig['shared']={'owned':self.source}
        self.rig['original']='opaque saved Original text; never parse this on draw'
        self.bpy=NS(types=NS(Object=Object))
        patcher=patch.dict(sys.modules,{'bpy':self.bpy})
        patcher.start();self.addCleanup(patcher.stop)
        self.ns={'json':json,'skirt':self.skirt}
        functions('skirt_original_mode.py', {'wiggle_original_context'},self.ns)

    def candidate(self, record=None):
        return self.ns['wiggle_original_context'](self.source,record)

    def test_shared_context_is_exact_owner_and_opaque_text_without_native_reads(self):
        self.assertEqual(self.candidate(),(self.rig,self.rig['original']))
        self.rig.data=None  # No Rest, bone arrays or mesh is needed by drawing.
        self.assertEqual(self.candidate(self.record),(self.rig,self.rig['original']))

    def test_actual_cloth_or_direct_setup_cannot_use_this_candidate_permission(self):
        for backend in ('LEGACY_CAGE','ACTUAL_SURFACE_DELTA_V1','DIRECT_MAIN_CLOTH_V1'):
            record=dict(self.record,physics={'backend':backend})
            self.assertIsNone(self.candidate(record))

    def test_foreign_shared_owner_is_not_a_candidate(self):
        self.rig['shared']['owned']=Object('Other','MESH')
        self.assertIsNone(self.candidate())

    def test_mesh_cannot_forge_the_original_owner(self):
        self.source['original']='not an armature session'
        self.assertIsNone(self.candidate())

    def test_missing_or_nontext_original_marker_is_not_a_candidate(self):
        for value in (None,{},True,0,''):
            self.rig['original']=value
            self.assertIsNone(self.candidate())

    def test_legacy_rig_requires_both_owner_and_native_data_binding(self):
        record=dict(self.record,shared=None)
        self.rig['owner']='owned';self.rig['source']=self.source
        self.assertEqual(self.candidate(record),(self.rig,self.rig['original']))
        self.rig.data['owner']='foreign'
        self.assertIsNone(self.candidate(record))

    def test_attached_original_requires_exact_display_owner_refs(self):
        owner=Object('Main')
        owner['original']='main original'
        self.rig.pop('original')
        self.rig['display_owner']=owner
        owner['character_designer_original_display_refs_v1']={'1':self.rig}
        self.assertEqual(self.candidate(),(owner,'main original'))
        owner['character_designer_original_display_refs_v1']={'1':Object('Replacement')}
        self.assertIsNone(self.candidate())

    def test_bad_source_record_never_becomes_authority(self):
        for value in ('not json','null','[]','{}'):
            self.source['record']=value
            self.assertIsNone(self.candidate())


class StrictOriginalTests(OriginalContextTests):
    def setUp(self):
        super().setUp()
        self.rig.data=Object('RigData','ARMATURE_DATA')
        self.rig.data['owner']='owned'
        self.rig.data.bones={'Physics':{'owner':'owned'}}
        self.scene=NS(objects={self.rig.name:self.rig})
        self.context=NS(scene=self.scene)
        self.saved={'version':1,'dress_edit':[{'owner':'owned'}], 'rest':{'Body':{}},'names':['Body']}
        self.entry={'owner':'owned','names':['Deform','Waist']}
        self.original=NS(SESSION='original', _require=Mock(return_value=self.saved),
                         _validate_session_rest=Mock(return_value=self.saved['rest']))
        self.owner=NS(pose_capability=Mock(return_value=object()))
        package=ModuleType('_original_wiggle_context_fixture')
        package.body_original_mode=self.original
        package.body_original_owner=self.owner
        patcher=patch.dict(sys.modules,{package.__name__:package})
        patcher.start();self.addCleanup(patcher.stop)
        self.ns.update(__package__=package.__name__,
            validate_active=Mock(), _resolve=Mock(return_value=[(self.entry,self.rig,self.source,self.record,())]))
        self.bpy.context=self.context
        functions('skirt_original_mode.py', {'require_wiggle_original'},self.ns)

    def require(self,names=('Physics',)):
        return self.ns['require_wiggle_original'](self.context,self.source,self.record,names)

    # The parent candidate tests are exercised by OriginalContextTests; these
    # strict cases deliberately replace data with actual bone-reference shape.
    def test_strict_proves_rest_and_original_graph_before_returning_token(self):
        self.assertEqual(self.require(),(self.rig,self.rig['original']))
        self.original._validate_session_rest.assert_called_once_with(self.rig,self.saved)
        self.ns['validate_active'].assert_called_once_with(self.context,self.rig,self.saved['dress_edit'])

    def test_original_ownership_failure_is_not_downgraded(self):
        self.original._require.side_effect=ValueError('foreign Original history')
        with self.assertRaisesRegex(ValueError,'foreign Original history'):self.require()

    def test_rest_failure_is_not_downgraded(self):
        self.original._validate_session_rest.side_effect=ValueError('Rest changed')
        with self.assertRaisesRegex(ValueError,'Rest changed'):self.require()

    def test_constraint_failure_is_not_downgraded(self):
        self.ns['validate_active'].side_effect=ValueError('constraint changed')
        with self.assertRaisesRegex(ValueError,'constraint changed'):self.require()

    def test_source_must_match_the_actual_original_inventory(self):
        self.ns['_resolve'].return_value=[(self.entry,self.rig,Object('Replacement','MESH'),self.record,())]
        with self.assertRaisesRegex(ValueError,'exact source and rig'):self.require()

    def test_physics_cannot_write_the_editable_deform_or_native_body_bones(self):
        for names in (('Deform',),('Body',)):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError,'separate generated bones'):
                self.require(names)

    def test_physics_names_cannot_be_empty_repeated_missing_or_foreign(self):
        for names in ((),('Physics','Physics'),('Missing',)):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError,'separate generated bones'):
                self.require(names)
        self.rig.data.bones['Physics']['owner']='foreign'
        with self.assertRaisesRegex(ValueError,'separate generated bones'):self.require()

    def test_a_changed_session_during_proof_is_refused(self):
        self.ns['validate_active'].side_effect=lambda *args:self.rig.__setitem__('original','replacement')
        with self.assertRaisesRegex(ValueError,'changed during physics verification'):self.require()

    def test_unupgraded_original_has_no_permission(self):
        self.saved.pop('dress_edit')
        with self.assertRaisesRegex(ValueError,'Complete this Original'):self.require()


class EntryHookTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        @contextmanager
        def defer(*args,**kwargs):
            self.events.append('begin')
            yield 'refresh'
            self.events.append('finalized')
        self.pending=Mock(side_effect=lambda *args:self.events.append('pending'))
        self.package=ModuleType('_original_entry_physics_fixture')
        self.package.forearm_twist=NS(defer_runtime=defer)
        self.package.body_original_owner=NS(pose_capability=Mock(return_value='capability'))
        self.ns={'__package__':self.package.__name__, 'display':NS(_apply_pending_physics=self.pending),
                 '_enter':Mock(side_effect=lambda *args,**kwargs:self.events.append('enter') or {'bones':3})}
        patcher=patch.dict(sys.modules,{self.package.__name__:self.package})
        patcher.start();self.addCleanup(patcher.stop)
        functions('body_original_mode.py',{'enter'},self.ns)

    def test_pending_runs_only_after_fully_finalized_original_entry(self):
        self.assertEqual(self.ns['enter']('context','rig'),{'bones':3})
        self.assertEqual(self.events,['begin','enter','finalized','pending'])

    def test_failed_original_entry_never_touches_pending_physics(self):
        self.ns['_enter'].side_effect=ValueError('Original failed')
        with self.assertRaisesRegex(ValueError,'Original failed'):self.ns['enter']('context','rig')
        self.pending.assert_not_called()

    def test_refused_pending_application_keeps_completed_original(self):
        self.pending.side_effect=lambda *args:{'failed':[{'message':'backend refused'}]}
        self.assertEqual(self.ns['enter']('context','rig'),{'bones':3})
        self.assertEqual(self.events,['begin','enter','finalized'])


class RunningContextTests(unittest.TestCase):
    def setUp(self):
        self.rig=Object('Rig');self.rig.mode='POSE';self.rig.data=NS(pose_position='POSE')
        self.rig.wiggle_mute=self.rig.wiggle_freeze=False
        self.rig.pose=NS(bones={'Physics':NS(wiggle_tail=True)})
        self.source=Object('Dress','MESH');self.scene=NS(wiggle_enable=True)
        self.backend=NS(wiggle_pre=lambda:None,wiggle_post=lambda:None)
        self.token=(self.rig,'verified original text')
        self.rig['original']=self.token[1]
        self.record={'owner':'owned'}
        self.runtime=NS(validate=Mock())
        package=ModuleType('_running_original_physics_fixture')
        package.wiggle2_runtime=self.runtime
        package.skirt_original_mode=NS(wiggle_original_context=Mock(side_effect=lambda source,record:
            (self.rig,self.rig['original']) if 'original' in self.rig else None))
        self.context_helper=package.skirt_original_mode.wiggle_original_context
        patcher=patch.dict(sys.modules,{package.__name__:package})
        patcher.start();self.addCleanup(patcher.stop)
        self.saved=dict(rig=self.rig,source=self.source,scene=self.scene,rig_name=self.rig.name,
            backend=self.backend,record=self.record,names=('Physics',),original_context=self.token,
            lease={'paused':False},paused=False)
        self.ns=dict(__package__=package.__name__,
            skirt=NS(_ORIGINAL_SESSION_KEY='original',ORIGINAL_DISPLAY_OWNER_KEY='display_owner',read_record=lambda source:self.record),
            bpy=NS(context=NS(scene=self.scene),data=NS(objects={self.source.name:self.source}),
                types=NS(Object=Object,PoseBone=NS(wiggle_tail=True)),
                app=NS(handlers=NS(frame_change_pre=[self.backend.wiggle_pre],frame_change_post=[self.backend.wiggle_post]))),
            _graph=Mock(return_value={'physics_influence':1.0}))
        functions('skirt_wiggle.py',{'_changed'},self.ns)

    def test_exact_verified_original_token_keeps_existing_native_guards(self):
        self.assertEqual(self.ns['_changed'](self.saved),'')
        self.runtime.validate.assert_called_once_with(self.saved['lease'])

    def test_changed_original_text_refuses_before_native_graph_or_runtime(self):
        self.rig['original']='replacement'
        self.assertIn('verified Original skirt session changed',self.ns['_changed'](self.saved))
        self.ns['_graph'].assert_not_called();self.runtime.validate.assert_not_called()

    def test_new_original_session_without_start_authority_is_refused(self):
        self.saved.pop('original_context')
        self.assertIn('verified Original skirt session changed',self.ns['_changed'](self.saved))

    def test_completed_original_return_keeps_running_lease(self):
        self.rig.pop('original')
        self.assertEqual(self.ns['_changed'](self.saved),'')
        self.runtime.validate.assert_called_once()

    def test_legacy_display_owner_is_checked_even_without_target_marker(self):
        self.rig.pop('original')
        main=Object('Main');main['original']='legacy original'
        self.rig['display_owner']=main
        self.context_helper.side_effect=None;self.context_helper.return_value=(main,'legacy original')
        self.saved['original_context']=(main,'legacy original')
        self.assertEqual(self.ns['_changed'](self.saved),'')
        self.context_helper.return_value=None
        self.assertIn('verified Original skirt session changed',self.ns['_changed'](self.saved))


if __name__=='__main__':unittest.main()
