"""Actual session witness guards; only native evaluation/edit boundaries mocked."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1] / 'tools/composition_session_acceptance.py'
SPEC = importlib.util.spec_from_file_location('actual_edit_a_c_session', PATH)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class EditACWitnessTests(unittest.TestCase):
    def setUp(self):
        self.frame, self.solo_seen, self.fault = 1., False, ''
        self.scope = NS(mapping=NS(indices={'Upper':0, 'Lower':1, 'Delegated':2}),
                        native_names=('Upper',), plan=NS(delegated_names=('Delegated',)))
        self.bdraft, self.bcustom = NS(motion='B'), NS(motion='B')
        self.acopy, self.adraft, self.source = NS(motion='A'), NS(motion='A'), NS(motion='A')
        self.rig = NS(animation_data=NS(use_tweak_mode=True, action=self.bdraft))
        self.item = NS(item_id='exact B native ID', rig=self.rig, custom_action=self.bcustom, custom_slot=1)
        self.a_item = NS(item_id='exact A native ID', custom_action=self.acopy, custom_slot=1)
        self.saved = NS(items=[self.item, self.a_item])
        self.session = {'owner':'exact session', 'rig':self.rig, 'spec':{'fps':30.},
            'edit_target':'B', 'solo':False, 'edit_action':self.bdraft, 'edit_slot':1,
            'base_action':self.source, 'base_slot':1, 'base_edit_action':self.adraft,
            'base_edit_slot':1, 'base_edit_item_id':self.a_item.item_id, 'base_custom_action':self.acopy}
        self.tracks = {'BASE':NS(mute=False,is_solo=False), 'EDIT':NS(mute=False,is_solo=False)}
        self.context = NS(scene=NS())
        self.backend = NS(validate=lambda _context,_rig:(self.session,self.item,self.scope,self.tracks),
            _motion_hash=lambda action,slot:action.motion if slot == 1 else 'wrong slot',
            set_edit_target=self.set_target, set_solo=self.set_solo)
        self.ua = NS(_set_frame=lambda _scene,frame:setattr(self,'frame',frame))
        self.worklist = NS(state=lambda _context:self.saved,
            _item=lambda _saved,item_id:next(row for row in self.saved.items if row.item_id==item_id))
        self.modules = {'animation_composition':self.backend,'unity_animation':self.ua,'animation_worklist':self.worklist}
        self.current_patch = patch.object(helper, '_current', return_value=(self.context,self.saved,self.item))
        self.current_mock = self.current_patch.start(); self.addCleanup(self.current_patch.stop)
        self.pose_patch = patch.object(helper, '_pose', side_effect=self.pose)
        self.pose_patch.start(); self.addCleanup(self.pose_patch.stop)

    def set_target(self, context, target, *, base_item_id=''):
        self.assertIs(context, self.context)
        if target == 'A':
            self.assertEqual(base_item_id,self.a_item.item_id)
        self.session['edit_target'] = target
        self.rig.animation_data.action = self.adraft if target=='A' else self.bdraft
        if target=='A' and self.fault=='native copy replacement':
            self.a_item.custom_action = NS(motion='A')
        if target=='B' and self.fault=='Custom changed on return':
            self.bcustom.motion = 'unexpected B change'

    def set_solo(self, context, enabled):
        self.session['solo'] = enabled
        self.solo_seen |= enabled
        self.tracks['EDIT'].mute = enabled

    def pose(self, context, rig, names):
        self.assertEqual(set(names),{'Upper','Lower'})
        result = {'Upper':[self.frame]*16,'Lower':[2*self.frame]*16}
        target = self.session['edit_target']
        if target=='A' and not self.solo_seen and self.fault in {'EditA upper','EditA lower'}:
            result['Upper' if self.fault=='EditA upper' else 'Lower'][0] += .001
        if target=='A' and self.solo_seen and self.fault=='Solo restoration':
            result['Upper'][0] += .001
        if target=='B' and self.solo_seen and self.fault=='ReturnB pose':
            result['Lower'][0] += .001
        return result

    def witness(self):
        return helper._edit_a_c_witness(self.context,self.item,self.a_item.item_id,self.modules,[.1,.4],2e-5)

    def test_actual_witness_measures_all_domains_and_retains_A_checkpoint_then_returns_B(self):
        with self.witness() as proof:
            self.assertEqual(self.session['edit_target'],'A')
            self.assertFalse(self.session['solo'])
            self.assertFalse(proof['completed'])
        self.assertTrue(proof['completed'])
        self.assertEqual(self.session['edit_target'],'B')
        self.assertEqual((proof['native_count'],proof['upper_count'],proof['lower_count'],proof['delegated_count']),(2,1,1,1))
        self.assertEqual(proof['action_hashes_before'],proof['action_hashes_after'])
        for row in proof['samples']:
            for stage in ('EditA','SoloA_restored_C','ReturnB'):
                self.assertEqual(row[stage],{'full':0.,'upper':0.,'lower':0.})
        self.assertTrue(proof['C_view_retained_while_editing_A'] and proof['C_view_retained_after_return_to_B'])
        self.assertTrue(proof['checkpoint_native_IDs_retained'])

    def test_actual_witness_rejects_upper_or_lower_pose_loss_while_editing_A(self):
        for fault in ('EditA upper','EditA lower'):
            with self.subTest(fault=fault):
                self.fault = fault
                self.session['edit_target']='B'; self.rig.animation_data.action=self.bdraft
                with self.assertRaisesRegex(AssertionError,'Native C changed during EditA'):
                    with self.witness(): pass

    def test_actual_witness_rejects_C_not_restored_after_Solo_A(self):
        self.fault='Solo restoration'
        with self.assertRaisesRegex(AssertionError,'SoloA_restored_C'):
            with self.witness(): pass

    def test_actual_witness_rejects_return_B_pose_or_Action_mutation(self):
        for fault in ('ReturnB pose','Custom changed on return'):
            with self.subTest(fault=fault):
                self.fault=fault; self.solo_seen=False; self.bcustom.motion='B'
                self.session['edit_target']='B'; self.rig.animation_data.action=self.bdraft
                with self.assertRaises(AssertionError):
                    with self.witness(): pass

    def test_actual_witness_rejects_coherent_equal_content_native_A_replacement(self):
        self.fault='native copy replacement'
        with self.assertRaisesRegex(AssertionError,'exact owned full C stack'):
            with self.witness(): pass

    def test_actual_witness_refuses_changed_A_draft_before_switch_would_commit(self):
        with self.assertRaisesRegex(AssertionError,'unmodified native work-copy draft'):
            with self.witness():
                self.adraft.motion='observer keyed A at checkpoint'
        self.assertEqual(self.session['edit_target'],'A')
        self.assertEqual(self.acopy.motion,'A')

    def test_checkpoint_native_replacement_rejects_before_retained_ID_access_or_commit(self):
        for replaced in ('Scene','Rig'):
            with self.subTest(replaced=replaced):
                self.session['edit_target']='B'; self.rig.animation_data.action=self.bdraft
                context,item = self.context,self.item
                if replaced=='Scene': context=NS(scene=NS())
                else: item=NS(rig=NS())
                with self.assertRaisesRegex(AssertionError,'original native Scene/Rig changed'):
                    with self.witness():
                        validation_count = Mock(wraps=self.backend.validate)
                        self.backend.validate=validation_count
                        self.current_mock.return_value=(context,self.saved,item)
                validation_count.assert_not_called()
                self.assertEqual(self.session['edit_target'],'A')
                self.assertEqual(self.acopy.motion,'A')
                self.current_mock.return_value=(self.context,self.saved,self.item)
                self.backend.validate=validation_count._mock_wraps

    def test_actual_witness_requires_unmodified_copy_and_real_B_stack(self):
        for fault in ('changed copy','no tweak','muted B','duplicate seek','excess tolerance'):
            with self.subTest(fault=fault):
                self.acopy.motion='A'; self.rig.animation_data.use_tweak_mode=True; self.tracks['EDIT'].mute=False
                seconds,tolerance=[.1,.4],2e-5
                if fault=='changed copy': self.acopy.motion='different A'
                if fault=='no tweak': self.rig.animation_data.use_tweak_mode=False
                if fault=='muted B': self.tracks['EDIT'].mute=True
                if fault=='duplicate seek': seconds=[.1,.1]
                if fault=='excess tolerance': tolerance=.001
                with self.assertRaises(AssertionError):
                    with helper._edit_a_c_witness(self.context,self.item,self.a_item.item_id,self.modules,seconds,tolerance): pass
                self.assertEqual(self.session['edit_target'],'B')


class PositiveAUpperWitnessTests(unittest.TestCase):
    @contextmanager
    def view(self,*_args):
        yield

    def run_positive(self,upper_delta=0.,lower_delta=.01):
        self.modified=False
        source,acopy,adraft,bcustom,bdraft=[NS(motion=value) for value in ('A','A','A','B','B')]
        bone=NS(name='Lower',rotation_mode='XYZ',hide=False,select=False,
            bone=NS(hide_select=False),lock_rotation=(False,False,False),
            path_from_id=lambda:'pose.bones["Lower"]')
        def keyframe(**_kwargs):
            adraft.motion='native keyed A'; return True
        bone.keyframe_insert=keyframe
        class BoneTable(dict):
            def __iter__(self): return iter(self.values())
        rig=NS(mode='POSE',pose=NS(bones=BoneTable(Lower=bone)),data=NS(bones=BoneTable(Lower=bone.bone)),
            animation_data=NS(use_tweak_mode=True,action=adraft))
        item=NS(item_id='native B',rig=rig,custom_action=bcustom,custom_slot=1)
        a_item=NS(item_id='native A',new_animation=True,source_action=None,custom_action=acopy,custom_slot=1,manifest_path='D:/actual-new-A.json')
        saved=NS(items=[item,a_item]); context=NS(scene=NS())
        session={'edit_target':'A','solo':False,'spec':{'fps':30.},'base_action':source,'base_slot':1,
                 'base_edit_action':adraft,'base_edit_slot':1}
        scope=NS(mapping=NS(indices={'Upper':0,'Lower':1,'Delegated':2}),native_names=('Upper',),plan=NS(delegated_names=('Delegated',)))
        def commit(*_args):
            acopy.motion=adraft.motion; self.modified=True; return True
        backend=NS(item_spec=lambda row:None if row is a_item else {'fps':30.},enter=Mock(),set_edit_target=Mock(),
            validate=lambda *_args:(session,item,scope,{}),_motion_hash=lambda action,slot:action.motion,
            _curves=lambda *_args:[NS(data_path='pose.bones["Lower"].rotation_euler',array_index=0)],
            _pose_selection_owner=lambda pb:pb,commit=commit)
        worklist=NS(state=lambda _context:saved,_item=lambda _saved,_id:a_item,_fresh_workspace=Mock())
        ua=NS(_snapshot=lambda *_args:{'protected':'snapshot'},_set_frame=Mock())
        modules={'animation_composition':backend,'animation_worklist':worklist,'unity_animation':ua,
                 'animation_worklist_new':NS(validate=lambda *_args:{'real origin':'unchanged'})}
        def poses(_context,_rig,names):
            self.assertEqual(set(names),{'Upper','Lower'})
            return {'Upper':[upper_delta if self.modified else 0.]*16,'Lower':[lower_delta if self.modified else 0.]*16}
        bpy=NS(ops=NS(transform=NS(rotate=Mock(return_value={'FINISHED'})),
            character_designer=NS(composition_close=Mock(return_value={'FINISHED'}))))
        with patch.dict(sys.modules,{'bpy':bpy}),patch.object(helper,'_view3d',self.view),patch.object(helper,'_sha',return_value='actual manifest hash'),\
             patch.object(helper,'_current',return_value=(context,saved,item)),patch.object(helper,'_pose',side_effect=poses):
            return helper._positive_a_edit(context,item,a_item.item_id,modules,{'poseTolerance':2e-5},[.1,.4])

    def test_actual_positive_A_key_commit_requires_lower_motion_and_unchanged_upper_B(self):
        result=self.run_positive()
        self.assertTrue(result['C_upper_B_unchanged'] and result['C_lower_changed'] and result['protected_Source_A_unchanged'])
        self.assertEqual(result['C_upper_error'],0.)
        self.assertGreater(result['C_lower_error'],0.)

    def test_actual_positive_A_key_rejects_lost_upper_B_even_when_lower_moves(self):
        with self.assertRaisesRegex(AssertionError,'upper-body B in C'):
            self.run_positive(upper_delta=.001)

    def test_actual_positive_A_key_rejects_no_effective_lower_motion(self):
        with self.assertRaisesRegex(AssertionError,'lower-body C pose'):
            self.run_positive(lower_delta=0.)


if __name__=='__main__':
    unittest.main()
