"""Translation math, selection/refusal and retained-publication contracts."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


PATH=Path(__file__).resolve().parents[1]/'addons/character_designer/controller_animation_move.py'
spec=importlib.util.spec_from_file_location('_controller_move_test',PATH)
move=importlib.util.module_from_spec(spec)
spec.loader.exec_module(move)


class Points(list):
    def foreach_get(self,field,values):
        for i,p in enumerate(self):
            values[2*i:2*i+2]=type(values)(values.typecode,getattr(p,field))

    def foreach_set(self,field,values):
        for i,p in enumerate(self):
            setattr(p,field,[float(values[2*i]),float(values[2*i+1])])


def point(time,value,interpolation='BEZIER'):
    pair=lambda x,y:[move._f32(x),move._f32(y)]
    return NS(co=pair(time,value),handle_left=pair(time-.5,value-.05),
              handle_right=pair(time+.5,value+.05),interpolation=interpolation,
              select_control_point=True,select_left_handle=False,select_right_handle=True,
              type='BREAKDOWN')


class Curve:
    def __init__(self,path,index,values,constant=False):
        self.data_path,self.array_index=path,index
        self.keyframe_points=Points([point(1,values[0]),point(10,values[1])])
        if constant:
            for p in self.keyframe_points:
                p.interpolation='CONSTANT'
                p.handle_left[1]=p.handle_right[1]=p.co[1]
        self.modifiers=[];self.sampled_points=[];self.mute=False;self.lock=False;self.is_driver=False
        self.group=NS(name='Artist Group',lock=False,mute=False,color=[.2,.4,.6])

    def evaluate(self,frame):
        a,b=self.keyframe_points
        return move._f32(a.co[1]+(b.co[1]-a.co[1])*(frame-a.co[0])/(b.co[0]-a.co[0]))


class Action(dict):
    def __init__(self,curves):
        super().__init__()
        self.name_full='Original Controller'
        self.users=1
        self.layers=[NS(strips=[NS(type='KEYFRAME',channelbags=[NS(slot_handle=7,fcurves=curves)])])]
        self.frame_range=(1.,10.)


class Bone(dict):
    def __init__(self,name,rig,location):
        super().__init__(ik_fk=1.)
        self.name,self.id_data,self.location=name,rig,location
        self.bone={'character_designer_limb_ik_role':'HAND_IK','switch_version':1}
        self.bone=DataBone(self.bone)
        self.lock_location=(False,False,False)

    def as_pointer(self):return id(self)
    def path_from_id(self,prop):return 'pose.bones['+json.dumps(self.name)+'].'+prop


class DataBone(dict):
    use_connect=False


class Bones(dict):
    def __iter__(self):return iter(self.values())


def payload(_api,curve):
    return dict(data_path=curve.data_path,array_index=curve.array_index,
        keyframe_points=[dict(co=list(p.co),co_ui=list(p.co),handle_left=list(p.handle_left),
            handle_right=list(p.handle_right),interpolation=p.interpolation,type=p.type,
            select_control_point=p.select_control_point,select_left_handle=p.select_left_handle,
            select_right_handle=p.select_right_handle) for p in curve.keyframe_points],
        artist_curve_settings=dict(lock=curve.lock,mute=curve.mute),
        artist_group_settings=copy.deepcopy(vars(curve.group)))


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.rig=NS(animation_data=NS(drivers=[]))
        self.target=Bone('Actual Target',self.rig,[move._f32(.65),move._f32(1.15),move._f32(2.55)])
        self.other=Bone('Unselected',self.rig,[9.,8.,7.])
        self.rig.pose=NS(bones=Bones({self.target.name:self.target,self.other.name:self.other}))
        path=self.target.path_from_id('location')
        self.curves=[Curve(path,i,(float(i),i+.9)) for i in range(3)]
        self.mode_path='pose.bones["Actual Target"]["ik_fk"]'
        self.curves.append(Curve(self.mode_path,0,(1.,1.),constant=True))
        self.curves.append(Curve(self.other.path_from_id('rotation_euler'),1,(.2,.3)))
        self.action=Action(self.curves)
        self.source=Action([])
        self.controller=NS(RECORD='controller_record',_read_record=lambda action:json.loads(action['controller_record']))
        self.action['controller_record']=json.dumps(dict(frame_start=1.,frame_end=10.,limbs=['ARM.L']))
        self.item=NS(side='CUSTOM',rig=self.rig,item_id='original',custom_action=self.action,custom_slot=7,
            source_action=self.source,source_slot=7,current_rig_revision='')
        self.current_item=self.item
        self.saved=NS(status='before',has_error=False)
        self.context=NS(mode='POSE',object=self.rig,selected_pose_bones=[self.target],
            scene=NS(frame_current=5,frame_subframe=.5,tool_settings=NS(use_keyframe_insert_auto=True)),view_layer=NS(update=Mock()))
        self.api=NS(limb=NS(_validate_inventory=Mock(return_value={'rigs':{('ARM','L'):{'target':self.target}}})),
            match=NS(VERSION_KEY='switch_version',VERSION=1,PROPERTY='ik_fk',property_path=lambda pb:self.mode_path))
        self.variant=dict(api=self.api,original=self.action,slot=7,frame=5.5,rig=self.rig,
            context=self.context,saved=self.saved,capture={'targets':[{'target':self.rig}]},phase='prepared',
            item_id='original',source=self.source,source_slot=7)
        fields=('item_id','rig','custom_action','custom_slot','source_action','source_slot')
        self.api.revision=NS(_IDENTITY_FIELDS=frozenset(fields),
            _row=lambda item:{key:getattr(item,key) for key in fields},validate=Mock())
        self.api.worklist=NS(current=lambda context:self.current_item,_item=lambda saved,key:self.item,
                            _check_baseline=Mock())
        self.api.target=NS(_freeze=copy.deepcopy,_validate_capture=Mock())
        self.variant['row_identity']=self.api.revision._row(self.item)
        self.offset=NS(prepare_variant=Mock(return_value=self.variant),_bag=lambda action,slot:action.layers[0].strips[0].channelbags[0],
            _variant_owner=Mock(),new_variant_action=Mock(side_effect=self.new),
            publish_variant=Mock(side_effect=self.publish),rollback_variant=Mock(side_effect=self.rollback))
        self.physics=NS(status=lambda context:{'hair':{},'dresses':[]})
        self.controller._raw=lambda rig,names:dict(object={},pose={name:dict(location=list(rig.pose.bones[name].location),
            mode=rig.pose.bones[name]['ik_fk']) for name in names},constraints={})
        self.controller._restore_raw=self.restore
        self.presentation='before'
        self.api.target._capture=lambda context,rig,**kwargs:dict(
            raw=self.controller._raw(rig,tuple(pb.name for pb in rig.pose.bones)),
            autokey=context.scene.tool_settings.use_keyframe_insert_auto,
            lock=self.target.lock_location,presentation=self.presentation)
        self.cache='before'
        self.controller._capture_display_checkpoint=Mock(side_effect=lambda rig:{'cache':self.cache})
        self.controller._validate_display_checkpoint=Mock()
        self.controller._restore_display_checkpoint=Mock(side_effect=lambda rig,saved:setattr(self,'cache',saved['cache']))
        package=NS(current=lambda context:self.current_item)
        module_name=move.__package__+'.animation_worklist' if move.__package__ else 'animation_worklist'
        # prepare imports its own Worklist; provide a tiny isolated package.
        move.__package__='_controller_move_fixture'
        import sys
        from types import ModuleType
        module=ModuleType(move.__package__)
        worklist=ModuleType(move.__package__+'.animation_worklist');worklist.current=package.current
        patched=patch.dict(sys.modules,{move.__package__:module,worklist.__name__:worklist})
        patched.start();self.addCleanup(patched.stop)
        mocked=patch.object(move,'_backend',return_value=(self.offset,self.controller,self.physics))
        mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(move,'_curve_payload',side_effect=payload)
        mocked.start();self.addCleanup(mocked.stop)
        self.offset._action_hash=lambda api,action,slot:move._digest(sorted(move._states(api,action)[0].items()))
        self.variant['hashes']={7:self.offset._action_hash(self.api,self.action,7)}
        self.variant['source_hash']=self.offset._action_hash(self.api,self.source,7)

    def new(self,variant):
        self.created=copy.deepcopy(self.action)
        self.variant['staged_action']=self.created
        return self.created

    def publish(self,variant,action,**_kwargs):
        self.variant['published']=True
        self.variant['owned_row_id']='new-retained-row'
        self.rig.animation_data.action=action;self.rig.animation_data.action_slot=NS(handle=7)
        self.current_item=NS(**vars(self.item))
        self.current_item.item_id='new-retained-row';self.current_item.custom_action=action
        # Simulate Action binding consuming pending unselected pose inputs.
        self.other.location=[0.,0.,0.]
        return dict(action=action,item_id='new-retained-row')

    def rollback(self,variant):
        self.variant['published']=False
        if variant.get('staged_action') is None:
            return  # Real prepared-only offset cancellation performs no restore.
        self.current_item=self.item
        self.rig.animation_data.action=self.action;self.rig.animation_data.action_slot=NS(handle=7)
        self.cache=None  # Native generic restore clears its display cache.

    def restore(self,rig,raw):
        for name,values in raw['pose'].items():
            rig.pose.bones[name].location=list(values['location'])
            rig.pose.bones[name]['ik_fk']=values['mode']

    def test_exact_vertical_translation_keeps_times_handles_UI_and_original(self):
        before=copy.deepcopy(payload(None,self.curves[0]))
        shifted=move.translate_key_payload(before,.25)
        self.assertEqual(payload(None,self.curves[0]),before)
        for a,b in zip(before['keyframe_points'],shifted['keyframe_points']):
            for field in ('co','co_ui','handle_left','handle_right'):
                self.assertEqual(b[field],[a[field][0],move._f32(a[field][1]+.25)])
            for field in ('interpolation','type','select_control_point','select_left_handle','select_right_handle'):
                self.assertEqual(a[field],b[field])
        self.assertEqual(shifted['artist_group_settings'],before['artist_group_settings'])

    def test_publish_keeps_unselected_pending_and_AutoKey_and_original_curves(self):
        before=move._states(self.api,self.action)[0]
        moved=list(self.target.location)
        plan=move.prepare(self.context)
        result=move.apply(plan)
        self.assertIsNot(result['action'],self.action)
        self.assertEqual(move._states(self.api,self.action)[0],before)
        self.assertEqual(self.other.location,[9.,8.,7.])
        self.assertEqual(self.target.location,moved)
        self.assertTrue(self.context.scene.tool_settings.use_keyframe_insert_auto)
        self.assertFalse(result['sparse_correction_layer'])

    def test_zero_delta_refuses_without_guessing_AutoKey_previous_value(self):
        self.target.location=[curve.evaluate(5.5) for curve in self.curves[:3]]
        with self.assertRaisesRegex(ValueError,'Auto Key already keyed'):
            move.prepare(self.context)
        self.offset.new_variant_action.assert_not_called()
        self.assertTrue(self.context.scene.tool_settings.use_keyframe_insert_auto)

    def test_explicit_selection_must_equal_actual_selection(self):
        with self.assertRaisesRegex(ValueError,'match the current Pose selection exactly'):
            move.prepare(self.context,targets=[self.other])
        self.offset.prepare_variant.assert_not_called()
        with self.assertRaisesRegex(ValueError,'actual IK hand or foot Pose targets'):
            move.prepare(self.context,targets=['Target'])
        self.offset.prepare_variant.assert_not_called()

    def test_non_target_and_incomplete_or_non_IK_curves_refuse(self):
        self.context.selected_pose_bones=[self.other]
        with self.assertRaisesRegex(ValueError,'actual IK hand or foot'):
            move.prepare(self.context)
        self.context.selected_pose_bones=[self.target]
        self.curves[3].keyframe_points[0].co[1]=0.
        with self.assertRaisesRegex(ValueError,'stay in IK'):
            move.prepare(self.context)
        self.curves[3].keyframe_points[0].co[1]=1.
        removed=self.curves.pop(2)
        with self.assertRaisesRegex(ValueError,'all three location'):
            move.prepare(self.context)
        self.curves.insert(2,removed)

    def test_curve_or_bone_locks_modifiers_samples_and_mute_refuse(self):
        for field,value in [('lock',True),('mute',True),('modifiers',[object()]),('sampled_points',[object()])]:
            old=getattr(self.curves[0],field);setattr(self.curves[0],field,value)
            try:
                with self.assertRaisesRegex(ValueError,'Unlock the target curves'):
                    move.prepare(self.context)
            finally:setattr(self.curves[0],field,old)
        self.target.lock_location=(True,False,False)
        with self.assertRaisesRegex(ValueError,'unlocked independent'):
            move.prepare(self.context)

    def test_every_real_write_publication_or_raw_fault_rolls_back_owned_copy(self):
        for phase in ('after_curve_translation','after_publication','after_raw_restore'):
            plan=move.prepare(self.context)
            old=list(self.target.location)
            def fault(current):
                if current==phase:raise RuntimeError('injected '+phase)
            with patch.object(move,'_fault',side_effect=fault):
                with self.assertRaisesRegex(RuntimeError,'injected'):
                    move.apply(plan)
            self.assertEqual(plan['phase'],'discarded')
            self.assertEqual(self.target.location,old)
            self.assertEqual(self.other.location,[9.,8.,7.])
            self.assertFalse(self.variant.get('published',False))
        self.assertEqual(self.offset.rollback_variant.call_count,3)

    def test_later_unselected_pending_or_curve_UI_change_refuses_stale_plan(self):
        plan=move.prepare(self.context)
        self.other.location[0]=11.
        with self.assertRaisesRegex(ValueError,'pending pose'):
            move.apply(plan)
        self.other.location[0]=9.
        plan=move.prepare(self.context)
        self.curves[-1].group.color=[.9,.2,.1]
        with self.assertRaisesRegex(ValueError,'Artist curve settings'):
            move.apply(plan)

    def test_prepared_discard_is_inert_and_repeated_discard_is_idempotent(self):
        plan=move.prepare(self.context)
        move.discard(plan)
        self.assertEqual(plan['phase'],'discarded')
        self.controller._validate_display_checkpoint.assert_not_called()
        self.controller._restore_display_checkpoint.assert_not_called()
        self.assertEqual(self.cache,'before')
        move.discard(plan)
        self.offset.rollback_variant.assert_called_once()

    def test_successful_owned_discard_restores_cache_and_pending_pose(self):
        before=list(self.target.location)
        plan=move.prepare(self.context);move.apply(plan);move.discard(plan)
        self.assertEqual(plan['phase'],'discarded');self.assertEqual(self.cache,'before')
        self.assertEqual(self.target.location,before);self.assertEqual(self.other.location,[9.,8.,7.])
        self.controller._validate_display_checkpoint.assert_called_once()
        self.controller._restore_display_checkpoint.assert_called_once()

    def test_published_foreign_curve_metadata_binding_source_or_pending_edits_are_kept(self):
        for kind in ('curve','metadata','binding','source','pending','autokey','locks','display','users'):
            with self.subTest(kind=kind):
                f=MoveTests('runTest');f.setUp()
                try:
                    plan=move.prepare(f.context);move.apply(plan)
                    if kind=='curve':f.created.layers[0].strips[0].channelbags[0].fcurves[0].keyframe_points[0].co[1]+=.01
                    elif kind=='metadata':f.created['new_artist_note']='keep this edit'
                    elif kind=='binding':f.rig.animation_data.action=f.action
                    elif kind=='source':f.source.layers[0].strips[0].channelbags[0].fcurves.append(Curve('location',0,(1.,2.)))
                    elif kind=='pending':f.other.location[0]+=1.
                    elif kind=='autokey':f.context.scene.tool_settings.use_keyframe_insert_auto=False
                    elif kind=='locks':f.target.lock_location=(True,False,False)
                    elif kind=='display':f.presentation='new artist choice'
                    else:f.created.users+=1
                    f.offset.rollback_variant.reset_mock()
                    with self.assertRaisesRegex(ValueError,'changed afterwards|unchanged Custom|settings changed'):
                        move.discard(plan)
                    self.assertEqual(plan['phase'],'published');f.offset.rollback_variant.assert_not_called()
                finally:f.doCleanups()

    def test_foreign_display_structure_stops_before_generic_rollback(self):
        plan=move.prepare(self.context);move.apply(plan)
        self.controller._validate_display_checkpoint.side_effect=ValueError('foreign collection')
        with self.assertRaisesRegex(ValueError,'foreign collection'):
            move.discard(plan)
        self.assertEqual(plan['phase'],'recovery_required')
        self.offset.rollback_variant.assert_not_called()
        self.assertIs(self.variant['staged_action'],self.created)

    def test_owned_unkeyed_pole_is_kept_and_later_pole_edit_refuses_stale_plan(self):
        pole=NS(pole_angle=.31)
        self.target.constraints={'owned':pole}
        self.variant['capture']['targets'][0]['constraints']={self.target.name:{'owned':{'pole_angle':.31}}}
        plan=move.prepare(self.context)
        pole.pole_angle=.32
        with self.assertRaisesRegex(ValueError,'pending pose'):
            move.apply(plan)
        pole.pole_angle=.31
        original_publish=self.offset.publish_variant.side_effect
        def publish(*args,**kwargs):
            result=original_publish(*args,**kwargs);pole.pole_angle=.1;return result
        self.offset.publish_variant.side_effect=publish
        move.apply(plan)
        self.assertEqual(pole.pole_angle,.31)


if __name__=='__main__':unittest.main()
