"""Controller admission/ownership checks without loading Blender or a scene.

Whole-clip equivalence and disposable-copy preservation have independent native
oracles. These checks cover bounded panel reads and refusal before Action writes.
"""
import importlib.util
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
PACKAGE = '_controller_service_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + '.controller_animation', ROOT / 'controller_animation.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
NS = types.SimpleNamespace
worklist = types.ModuleType(PACKAGE + '.animation_worklist')
worklist.current = lambda context: context.item
sys.modules[worklist.__name__] = worklist


class Action(dict):
    library = None
    asset_data = None
    users = 0
    use_fake_user = False

    @property
    def frame_range(self):
        raise AssertionError('Panel/status scanned animation data.')

    @property
    def layers(self):
        raise AssertionError('Panel/status read Action layers.')


def fixture():
    action = Action()
    slot = NS(handle=7)
    rig = NS(animation_data=NS(action=action, action_slot=slot))
    item = NS(rig=rig, side='CUSTOM', custom_action=action, custom_slot=7,
              new_animation=False, composition_json='')
    return NS(item=item,scene=NS(unit_settings=NS(scale_length=1.))), action


class WorldMatrix:
    """Independent small affine fixture, with a deliberate shear/TRS blind spot."""
    def __init__(self, rows, decomposition=None):
        self.rows=rows
        self.decomposition=decomposition

    @classmethod
    def trs(cls, location=(0.,0.,0.), angle=0., scale=(1.,1.,1.)):
        c,s=math.cos(angle),math.sin(angle)
        return cls([[c*scale[0],-s*scale[1],0.,location[0]],
                    [s*scale[0],c*scale[1],0.,location[1]],
                    [0.,0.,scale[2],location[2]],[0.,0.,0.,1.]])

    def __getitem__(self,index):
        return self.rows[index]

    def __matmul__(self,other):
        return WorldMatrix([[sum(self[i][k]*other[k][j] for k in range(4)) for j in range(4)] for i in range(4)])

    def decompose(self):
        if self.decomposition is not None:
            return self.decomposition
        location=tuple(self[i][3] for i in range(3))
        scale=tuple(math.hypot(*(self[i][j] for i in range(3))) for j in range(3))
        angle=math.atan2(self[1][0],self[0][0])
        return location,(math.cos(angle/2),0.,0.,math.sin(angle/2)),scale


class ControllerServiceTest(unittest.TestCase):
    def _policy(self, quality='EDITABLE', scale=1.):
        return service._quality_policy(NS(scene=NS(unit_settings=NS(scale_length=scale))),quality)

    def test_world_quaternion_angle_normalizes_sign_scale_and_shortest_arc(self):
        theta=.004
        q=(math.cos(theta/2),0.,math.sin(theta/2),0.)
        self.assertAlmostEqual(service._quaternion_angle((3.,0.,0.,0.),tuple(-7.*v for v in q)),theta,places=14)
        self.assertEqual(service._quaternion_angle(q,tuple(-v for v in q)),0.)
        a=(math.cos(math.radians(179)/2),0.,0.,math.sin(math.radians(179)/2))
        b=(math.cos(math.radians(-179)/2),0.,0.,math.sin(math.radians(-179)/2))
        self.assertAlmostEqual(service._quaternion_angle(a,b),math.radians(2),places=14)
        for bad in ((0.,0.,0.,0.),(float('nan'),0.,0.,0.)):
            with self.assertRaises(ValueError):
                service._quaternion_angle(bad,q)

    def test_world_playback_position_includes_object_scale_and_scene_meters(self):
        world=WorldMatrix.trs(scale=(2.,2.,2.))
        expected=(world,{'hand':WorldMatrix.trs()})
        actual=(world,{'hand':WorldMatrix.trs(location=(.75,0.,0.))})
        errors=service._world_playback_error(actual,expected,.001)
        self.assertAlmostEqual(errors['position_meters'],.0015)
        self.assertFalse(service._playback_acceptable(errors,self._policy(scale=.001)))
        errors=service._world_playback_error(actual,expected,.0005)
        self.assertAlmostEqual(errors['position_meters'],.00075)
        self.assertTrue(service._playback_acceptable(errors,self._policy(scale=.0005)))

    def test_world_playback_compares_object_motion_even_without_bones(self):
        expected=(WorldMatrix.trs(),{})
        actual=(WorldMatrix.trs(location=(.0011,0.,0.)),{})
        errors=service._world_playback_error(actual,expected,1.)
        self.assertAlmostEqual(errors['position_meters'],.0011)
        self.assertFalse(service._playback_acceptable(errors,self._policy()))

    def test_editable_playback_keeps_shape_after_removing_each_rotation(self):
        expected=(WorldMatrix.trs(),{})
        actual=(WorldMatrix.trs(angle=.004,scale=(1.0001,1.0001,1.0001)),{})
        errors=service._world_playback_error(actual,expected,1.)
        self.assertGreater(errors['world_matrix'],service.MATRIX_TOLERANCE)
        self.assertAlmostEqual(errors['rotation_radians'],.004,places=14)
        self.assertAlmostEqual(errors['scale_relative'],.0001,places=12)
        self.assertLessEqual(errors['shape_relative'],2.e-4)
        self.assertTrue(service._playback_acceptable(errors,self._policy()))

    def test_same_decomposed_trs_but_changed_shear_fails_editable_shape_gate(self):
        identity=WorldMatrix.trs()
        sheared=WorldMatrix([[1.,.01,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.],[0.,0.,0.,1.]],
                            decomposition=((0.,0.,0.),(1.,0.,0.,0.),(1.,1.,1.)))
        errors=service._world_playback_error((sheared,{}),(identity,{}),1.)
        self.assertEqual((errors['position_meters'],errors['rotation_radians'],errors['scale_relative']),(0.,0.,0.))
        self.assertAlmostEqual(errors['shape_relative'],.01)
        self.assertFalse(service._playback_acceptable(errors,self._policy()))

    def test_editable_budget_boundaries_and_each_independent_failure(self):
        policy=self._policy()
        errors=dict(position_meters=.001,rotation_radians=.004363323129985824,
                    scale_relative=2.e-4,shape_relative=2.e-4,world_matrix=99.,matrix=99.)
        self.assertTrue(service._playback_acceptable(errors,policy))
        for key in ('position_meters','rotation_radians','scale_relative','shape_relative'):
            with self.subTest(key=key):
                failed=dict(errors)
                failed[key]=math.nextafter(failed[key],math.inf)
                self.assertFalse(service._playback_acceptable(failed,policy))

    def test_strict_playback_and_sample_solver_keep_original_matrix_gate(self):
        raw=dict(matrix=2.e-4,position=2.e-4,rotation=2.e-3,scale=2.e-4)
        self.assertEqual(service.MATRIX_TOLERANCE,2.e-4)
        self.assertTrue(service._acceptable(raw))
        raw.update(position_meters=0.,rotation_radians=0.,scale_relative=0.,shape_relative=0.)
        self.assertTrue(service._playback_acceptable(raw,self._policy('STRICT')))
        raw['matrix']=math.nextafter(2.e-4,math.inf)
        self.assertFalse(service._acceptable(raw))
        self.assertFalse(service._playback_acceptable(raw,self._policy('STRICT')))
        self.assertTrue(service._playback_acceptable(raw,self._policy()))

    def test_world_singular_nonfinite_and_bad_scene_units_refuse(self):
        for matrix in (WorldMatrix.trs(scale=(1.,0.,1.)),WorldMatrix.trs(location=(float('inf'),0.,0.))):
            with self.assertRaises(ValueError):
                service._world_playback_error((matrix,{}),(WorldMatrix.trs(),{}),1.)
        for scale in (0.,-1.,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):
                self._policy(scale=scale)
        with self.assertRaisesRegex(ValueError,'EDITABLE or STRICT'):
            self._policy('UNKNOWN')

    def test_actual_action_verify_uses_quality_only_after_sample_solver(self):
        context=NS(view_layer=NS(update=Mock()))
        actual=(WorldMatrix.trs(location=(.0005,0.,0.)),{})
        expected=(WorldMatrix.trs(),{})
        raw=dict(matrix=.0005,position=.0005,rotation=0.,scale=0.)
        for quality,failed_count in (('EDITABLE',2),('STRICT',5)):
            with self.subTest(quality=quality), \
                 patch.object(service,'_restore_raw'),patch.object(service,'_frame'), \
                 patch.object(service,'_pose',side_effect=lambda _ctx,rig,_names:actual if rig=='new' else expected), \
                 patch.object(service,'_error',return_value=dict(raw)):
                policy=self._policy(quality)
                worst,failed=service._verify(context,object(),'source','new',{},
                    dict(native_names=(),playback_policy=policy),(1.,2.))
                self.assertEqual(len(failed),failed_count)
                self.assertEqual(worst['matrix'],.0005)
                if quality=='EDITABLE':
                    self.assertEqual(worst['position_meters'],.0005)
                    self.assertEqual(worst['sample_matrix'],.0005)
                    self.assertEqual(failed,(1.,2.))
                else:
                    self.assertNotIn('position_meters',worst)

    def test_editable_written_samples_stay_strict_while_only_interval_probes_use_budget(self):
        context=NS(view_layer=NS(update=Mock()))
        samples=(1.,2.,3.)  # All matched samples, even if the candidate retains only endpoint keys.
        for sample_error in (True,False):
            with self.subTest(sample_error=sample_error):
                current=[None]
                def frame(_scene,value):
                    current[0]=value
                def pose(_context,rig,_names):
                    selected=current[0]==2. if sample_error else current[0] not in samples
                    distance=.0005 if rig=='new' and selected else 0.
                    return WorldMatrix.trs(location=(distance,0.,0.)),{}
                def native_error(actual,expected):
                    distance=abs(actual[0][0][3]-expected[0][0][3])
                    return dict(matrix=distance,position=distance,rotation=0.,scale=0.)
                with patch.object(service,'_restore_raw'),patch.object(service,'_frame',side_effect=frame), \
                     patch.object(service,'_pose',side_effect=pose),patch.object(service,'_error',side_effect=native_error):
                    worst,failed=service._verify(context,object(),'source','new',{},
                        dict(native_names=(),playback_policy=self._policy()),samples)
                self.assertEqual(worst['matrix'],.0005)
                self.assertEqual(worst['position_meters'],.0005)
                self.assertEqual(worst['sample_matrix'],.0005 if sample_error else 0.)
                self.assertEqual(failed,(2.,) if sample_error else ())

    def test_strict_verify_does_not_run_new_world_singularity_or_shape_gate(self):
        context=NS(view_layer=NS(update=Mock()))
        singular=(WorldMatrix.trs(scale=(1.,0.,1.)),{})
        with patch.object(service,'_restore_raw'),patch.object(service,'_frame'), \
             patch.object(service,'_pose',return_value=singular), \
             patch.object(service,'_error',return_value=dict(matrix=0.,position=0.,rotation=0.,scale=0.)) as legacy, \
             patch.object(service,'_world_playback_error',side_effect=AssertionError('STRICT entered a new world gate.')) as world:
            worst,failed=service._verify(context,object(),'source','new',{},
                dict(native_names=(),playback_policy=self._policy('STRICT')),(1.,2.))
        self.assertEqual(worst,dict(matrix=0.,position=0.,rotation=0.,scale=0.))
        self.assertEqual(failed,())
        self.assertEqual(legacy.call_count,5)
        self.assertFalse(world.called)

    def test_publish_refuses_changed_units_before_any_variant_or_display_write(self):
        context=NS(scene=NS(unit_settings=NS(scale_length=1.)))
        action,payload=object(),{'metadata':{},'diagnostics':{}}
        plan=dict(phase='built',variant=dict(staged_action=action),result=payload,
                  source=dict(context=context,quality='EDITABLE',playback_policy=self._policy()))
        built=dict(action=action,result=payload)
        offset=NS(publish_variant=Mock())
        display=NS(sync_limb_display=Mock())
        context.scene.unit_settings.scale_length=.01
        with patch.dict(sys.modules,{PACKAGE+'.control_pose_offset':offset,PACKAGE+'.bone_collections':display}):
            with self.assertRaisesRegex(ValueError,'length units changed after building'):
                service.publish(plan,built)
        self.assertEqual(plan['phase'],'built')
        self.assertFalse(offset.publish_variant.called or display.sync_limb_display.called)
        self.assertEqual(context.scene.unit_settings.scale_length,.01)

    def test_one_shot_conversion_discards_stale_units_result_by_existing_outer_transaction(self):
        context=NS(scene=NS(unit_settings=NS(scale_length=1.)))
        action,payload=object(),{'metadata':{},'diagnostics':{}}
        plan=dict(phase='prepared',variant=dict(staged_action=action),result=payload,
                  source=dict(context=context,quality='EDITABLE',playback_policy=self._policy()))
        built=dict(action=action,result=payload)
        def build(_plan):
            plan['phase']='built'
            context.scene.unit_settings.scale_length=.01
            return built
        with patch.object(service,'prepare',return_value=plan),patch.object(service,'build',side_effect=build), \
             patch.object(service,'discard') as discard:
            with self.assertRaisesRegex(ValueError,'length units changed after building'):
                service.convert_current_action(context)
        discard.assert_called_once_with(plan)

    def test_public_conversion_quality_is_forwarded_with_editable_default(self):
        for quality in ('EDITABLE','STRICT'):
            with patch.object(service,'convert_current_action',return_value='done') as convert:
                if quality=='EDITABLE':
                    self.assertEqual(service.convert('ctx','rig'),'done')
                else:
                    self.assertEqual(service.convert('ctx','rig',quality=quality),'done')
                convert.assert_called_once_with('ctx','rig',activate=True,quality=quality)
            with patch.object(service,'prepare',return_value={'phase':'prepared'}) as prepare, \
                 patch.object(service,'build',return_value='built'), \
                 patch.object(service,'publish',return_value='published'):
                self.assertEqual(service.convert_current_action('ctx','rig',quality=quality),'published')
                prepare.assert_called_once_with('ctx','rig',frame_start=None,frame_end=None,quality=quality)

    def test_prepare_and_build_keep_quality_and_captured_units(self):
        context,action=fixture()
        variant=dict(original=action,slot=7,frame=1.)
        for quality in ('EDITABLE','STRICT'):
            source=dict(conversion_state='CONTROLLER',context=context,rig=context.item.rig,action=action,slot=7,
                        quality=quality,playback_policy=self._policy(quality))
            offset=NS(prepare_variant=Mock(return_value=variant),_variant_owner=Mock(),new_variant_action=Mock(return_value='copy'))
            with patch.object(service,'_api',return_value=(offset,NS(worklist=worklist),None,None,None)), \
                 patch.object(service,'_source_plan',return_value=source) as planned, \
                 patch.object(service,'_pending_keyed_inputs',return_value=()), \
                 patch.object(service,'_capture_display_checkpoint',return_value='checkpoint'), \
                 patch.object(service,'build_controller_channels',return_value='channels') as reader, \
                 patch.object(service,'apply_to_action'):
                plan=service.prepare(context,quality=quality)
                self.assertEqual(planned.call_args.kwargs,{'quality':quality})
                self.assertEqual(plan['quality'],quality)
                self.assertEqual(service.build(plan),dict(action='copy',result='channels'))
                reader.assert_called_once_with(context,context.item.rig,action,7,quality=quality)
                plan['phase']='prepared'
                context.scene.unit_settings.scale_length=.01
                with self.assertRaisesRegex(ValueError,'length units changed'):
                    service.build(plan)
                context.scene.unit_settings.scale_length=1.

    def test_private_evaluation_guard_does_not_import_absent_hair_modules(self):
        with patch.dict(sys.modules):
            sys.modules.pop(PACKAGE+'.hair_wiggle2_adapter',None)
            sys.modules.pop(PACKAGE+'.hair_wiggle_adapter',None)
            before=set(sys.modules)
            service._require_private_evaluation_idle()
            self.assertEqual(set(sys.modules),before)

    def test_private_evaluation_guard_allows_none_or_explicitly_paused_v2_without_writes(self):
        for session in (None,{'physics_paused':True,'armature':object()}):
            with self.subTest(session=session):
                module=NS(_SESSION=session)
                before=None if session is None else dict(session)
                with patch.dict(sys.modules,{PACKAGE+'.hair_wiggle2_adapter':module,
                                             PACKAGE+'.hair_wiggle_adapter':NS(_SESSION=None)}):
                    service._require_private_evaluation_idle()
                self.assertIs(module._SESSION,session)
                self.assertEqual(session,before)

    def test_private_evaluation_guard_refuses_running_or_unproven_v2_session(self):
        for session in ({},{'physics_paused':False},{'physics_paused':1},
                        {'physics_paused':'True'},object()):
            with self.subTest(session=session):
                module=NS(_SESSION=session)
                before=dict(session) if isinstance(session,dict) else session
                with patch.dict(sys.modules,{PACKAGE+'.hair_wiggle2_adapter':module,
                                             PACKAGE+'.hair_wiggle_adapter':NS(_SESSION=None)}):
                    with self.assertRaisesRegex(ValueError,'Pause Hair Physics'):
                        service._require_private_evaluation_idle()
                self.assertIs(module._SESSION,session)
                self.assertEqual(session,before)

    def test_private_evaluation_guard_refuses_other_character_legacy_even_if_paused(self):
        for session in ({},{'physics_paused':True,'armature':object()}):
            with self.subTest(session=session):
                legacy=NS(_SESSION=session)
                before=dict(session)
                with patch.dict(sys.modules,{PACKAGE+'.hair_wiggle2_adapter':NS(_SESSION={'physics_paused':True}),
                                             PACKAGE+'.hair_wiggle_adapter':legacy}):
                    with self.assertRaisesRegex(ValueError,'legacy Hair preview'):
                        service._require_private_evaluation_idle()
                self.assertIs(legacy._SESSION,session)
                self.assertEqual(session,before)

    def test_current_conversion_hair_refusal_precedes_api_capture_and_builder(self):
        session={'physics_paused':False,'armature':object()}
        with patch.dict(sys.modules,{PACKAGE+'.hair_wiggle2_adapter':NS(_SESSION=session)}), \
             patch.object(service,'_api') as api, \
             patch.object(service,'_source_plan') as source, \
             patch.object(service,'_capture_display_checkpoint') as checkpoint, \
             patch.object(service,'build_controller_channels') as builder:
            with self.assertRaisesRegex(ValueError,'Pause Hair Physics'):
                service.prepare(object())
        self.assertFalse(api.called or source.called or checkpoint.called or builder.called)
        self.assertIs(session['physics_paused'],False)

    def test_explicit_source_hair_refusal_precedes_source_planning_or_blender_import(self):
        session={'physics_paused':False,'armature':object()}
        with patch.dict(sys.modules,{PACKAGE+'.hair_wiggle2_adapter':NS(_SESSION=session)}), \
             patch.object(service,'_api') as api, \
             patch.object(service,'_source_plan') as source:
            with self.assertRaisesRegex(ValueError,'Pause Hair Physics'):
                service.build_controller_channels(object(),object(),object(),7)
        self.assertFalse(api.called or source.called)
        self.assertIs(session['physics_paused'],False)

    def _pending_fixture(self, actual, expected):
        parser_spec=importlib.util.spec_from_file_location('_controller_pending_curve_parser',ROOT/'control_pose_offset.py')
        offset=importlib.util.module_from_spec(parser_spec)
        parser_spec.loader.exec_module(offset)
        path='pose.bones["finger"].rotation_quaternion'
        values={path:list(actual)}
        rig=NS(name='Private Rig',rotation_mode='XYZ',
               pose=NS(bones={'finger':NS(rotation_mode='QUATERNION')}),
               animation_data=NS(drivers=[]),path_resolve=lambda key:values[key])
        curves=[NS(data_path=path,array_index=i,mute=False,group=None,
                   keyframe_points=[object()],sampled_points=[],modifiers=[],
                   evaluate=Mock(return_value=value)) for i,value in enumerate(expected)]
        plan=dict(rig=rig,action=object(),slot=7,closure={'finger'},secondary=[])
        inputs=NS(_curves=lambda *_args:curves)
        return plan,curves,values,inputs,offset

    def test_unselected_keyed_quaternion_pending_refuses_until_inserted(self):
        import math
        actual=(math.cos(.017/2),0.,math.sin(.017/2),0.)
        plan,curves,values,inputs,offset=self._pending_fixture(actual,(1.,0.,0.,0.))
        before={key:list(value) for key,value in values.items()}
        modules={PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}
        with patch.dict(sys.modules,modules):
            self.assertEqual(service._pending_keyed_inputs(plan,13.),('finger',))
            for curve,value in zip(curves,actual):
                curve.evaluate.return_value=value  # The committed native-key case.
            self.assertEqual(service._pending_keyed_inputs(plan,13.),())
        self.assertEqual(values,before)

    def test_quaternion_sign_and_normalization_are_not_false_pending_edits(self):
        plan,curves,_values,inputs,offset=self._pending_fixture((-1.,0.,0.,0.),(2.,0.,0.,0.))
        with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
            self.assertEqual(service._pending_keyed_inputs(plan,1.),())

    def _body_raw_driver(self, plan, path):
        key=('pose.bones["finger"].constraints["owned"].influence',0)
        driver=NS(data_path=key[0],array_index=key[1],mute=False,
                  driver=NS(variables=[NS(type='SINGLE_PROP',targets=[NS(id=plan['rig'],data_path=path)])]))
        plan['rig'].animation_data.drivers=[driver]
        plan['drivers']={key}
        return driver

    def test_body_driver_raw_quaternion_dependency_keeps_numeric_sign_and_scale(self):
        for actual in ((-1.,0.,0.,0.),(2.,0.,0.,0.)):
            with self.subTest(actual=actual):
                plan,_curves,_values,inputs,offset=self._pending_fixture(actual,(1.,0.,0.,0.))
                self._body_raw_driver(plan,'pose.bones["finger"].rotation_quaternion[0]')
                with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
                    self.assertEqual(service._pending_keyed_inputs(plan,1.),('finger',))

    def test_body_driver_reads_inactive_rotation_component_without_false_skip(self):
        plan,curves,values,inputs,offset=self._pending_fixture((1.,0.,0.,0.),(1.,0.,0.,0.))
        path='pose.bones["finger"].rotation_euler'
        values[path]=[0.,.02,0.]
        curves.append(NS(data_path=path,array_index=1,mute=False,group=None,
                         keyframe_points=[object()],sampled_points=[],modifiers=[],evaluate=Mock(return_value=0.)))
        driver=self._body_raw_driver(plan,path+'[1]')
        with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
            self.assertEqual(service._pending_keyed_inputs(plan,1.),('finger',))
            driver.mute=True
            self.assertEqual(service._pending_keyed_inputs(plan,1.),())

    def test_necessary_object_numeric_input_is_checked_only_for_authenticated_body_driver(self):
        plan,curves,values,inputs,offset=self._pending_fixture((1.,0.,0.,0.),(1.,0.,0.,0.))
        path='["controller_weight"]'
        values[path]=.2
        curves.append(NS(data_path=path,array_index=0,mute=False,group=None,
                         keyframe_points=[object()],sampled_points=[],modifiers=[],evaluate=Mock(return_value=0.)))
        self._body_raw_driver(plan,path)
        with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
            self.assertEqual(service._pending_keyed_inputs(plan,1.),('Object Private Rig',))
            plan['drivers']=set()
            self.assertEqual(service._pending_keyed_inputs(plan,1.),())

    def test_necessary_bone_data_numeric_input_is_checked_only_for_authenticated_body_driver(self):
        for array in (False,True):
            with self.subTest(array=array):
                plan,curves,values,inputs,offset=self._pending_fixture((1.,0.,0.,0.),(1.,0.,0.,0.))
                path='data.bones["finger"]["controller_weight"]'
                values[path]=[0.,.2] if array else .2
                curves.append(NS(data_path=path,array_index=int(array),mute=False,group=None,
                                 keyframe_points=[object()],sampled_points=[],modifiers=[],evaluate=Mock(return_value=0.)))
                plan['rig'].data=object()
                driver=self._body_raw_driver(plan,path)
                driver.driver.variables[0].targets[0]=NS(id=plan['rig'].data,
                    data_path='bones["finger"]["controller_weight"]'+('[1]' if array else ''))
                with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
                    self.assertEqual(service._pending_keyed_inputs(plan,1.),('finger',))
                    plan['drivers']=set()
                    self.assertEqual(service._pending_keyed_inputs(plan,1.),())

    def test_pending_check_skips_muting_drivers_and_inactive_rotation(self):
        plan,curves,values,inputs,offset=self._pending_fixture((1.,0.,.01,0.),(1.,0.,0.,0.))
        curves[2].mute=True
        curves[0].group=NS(mute=True)
        plan['rig'].animation_data.drivers=[NS(data_path=curves[1].data_path,array_index=1,mute=False)]
        inactive='pose.bones["finger"].rotation_euler'
        values[inactive]=[.1,.2,.3]
        curves.append(NS(data_path=inactive,array_index=0,mute=False,group=None,
                         keyframe_points=[object()],sampled_points=[],modifiers=[],evaluate=Mock(return_value=0.)))
        with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
            self.assertEqual(service._pending_keyed_inputs(plan,1.),())
        self.assertFalse(curves[-1].evaluate.called)

    def test_pending_guard_uses_evaluated_modifier_value_without_reading_key_arrays(self):
        plan,curves,_values,inputs,offset=self._pending_fixture((1.,0.,0.,0.),(1.,0.,0.,0.))
        for curve in curves:
            curve.keyframe_points=[]
            curve.modifiers=[NS(mute=False)]
        with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
            self.assertEqual(service._pending_keyed_inputs(plan,3.25),())
        self.assertTrue(all(curve.evaluate.call_args.args==(3.25,) for curve in curves))

    def test_secondary_simulation_is_not_reported_as_body_pending(self):
        plan,_curves,_values,inputs,offset=self._pending_fixture((1.,0.,.01,0.),(1.,0.,0.,0.))
        plan['secondary']=['finger']
        with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
            self.assertEqual(service._pending_keyed_inputs(plan,1.),())

    def test_pending_numeric_tolerance_is_float32_ulps_not_a_pose_budget(self):
        self.assertTrue(service._same_input_value(1.,1.+2.**-24))
        self.assertTrue(service._same_input_value(-0.,0.))
        self.assertFalse(service._same_input_value(1.,1.00001))
        self.assertFalse(service._same_input_value(0.,1.e-8))

    def test_integer_and_boolean_raw_inputs_require_exact_evaluated_values(self):
        for actual,expected,pending in ((2**40+1,float(2**40),True),(2**40,float(2**40),False),
                                        (True,1.-2.**-23,True),(True,1.,False),(7,7.01,True)):
            with self.subTest(actual=actual,expected=expected):
                plan,curves,values,inputs,offset=self._pending_fixture((1.,0.,0.,0.),(1.,0.,0.,0.))
                path='["controller_weight"]'
                values[path]=actual
                curves.append(NS(data_path=path,array_index=0,mute=False,group=None,
                    keyframe_points=[object()],sampled_points=[],modifiers=[],evaluate=Mock(return_value=expected)))
                self._body_raw_driver(plan,path)
                with patch.dict(sys.modules,{PACKAGE+'.animation_export_inputs':inputs,PACKAGE+'.control_pose_offset':offset}):
                    self.assertEqual(service._pending_keyed_inputs(plan,1.),('Object Private Rig',) if pending else ())

    def test_high_level_prepare_refuses_pending_before_any_builder_or_action_copy(self):
        context,action=fixture()
        variant={'original':action,'slot':7,'frame':13.}
        offset=NS(prepare_variant=Mock(return_value=variant))
        api=NS(worklist=worklist)
        with patch.object(service,'_api',return_value=(offset,api,None,None,None)), \
             patch.object(service,'_source_plan',return_value={'conversion_state':'CONTROLLER'}), \
             patch.object(service,'_pending_keyed_inputs',return_value=('finger',)), \
             patch.object(service,'_capture_display_checkpoint') as checkpoint, \
             patch.object(service,'build_controller_channels',side_effect=AssertionError('Builder entered on pending input.')) as builder:
            with self.assertRaisesRegex(ValueError,'Insert keyframes.*current pose was kept'):
                service.prepare(context,context.item.rig)
        self.assertFalse(builder.called)
        self.assertFalse(checkpoint.called)
        self.assertEqual(action,{})

    def _display_fixture(self):
        bones=[NS(name=name,as_pointer=lambda i=i:100+i) for i,name in enumerate(('a','b','c'))]
        collections=[]
        for i,members in enumerate((bones,bones[:1])):
            collection=NS(name='collection '+str(i),parent=collections[0] if i else None,
                          as_pointer=lambda i=i:40+i,bones=list(members))
            collection.assign=Mock(side_effect=lambda bone,c=collection:c.bones.append(bone))
            collection.unassign=Mock(side_effect=lambda bone,c=collection:c.bones.remove(bone))
            collections.append(collection)
        rig=NS(as_pointer=lambda:17,data=NS(as_pointer=lambda:29,collections_all=collections))
        display=NS(_FRAME_CACHE={17:('old cache',),99:('foreign cache',)},_FRAME_OWNER_REJECTIONS={})
        return rig,display,collections,bones

    def test_display_checkpoint_is_read_only_and_keeps_native_member_order(self):
        rig,display,collections,bones=self._display_fixture()
        with patch.dict(sys.modules,{PACKAGE+'.bone_collections':display}):
            saved=service._capture_display_checkpoint(rig)
        self.assertIs(saved['data'],rig.data)
        self.assertIs(saved['collections'][0]['ref'],collections[0])
        self.assertEqual(saved['collections'][0]['members'],tuple(bones))
        self.assertTrue(all(not c.assign.called and not c.unassign.called for c in collections))
        self.assertEqual(display._FRAME_OWNER_REJECTIONS,{})

    def test_published_discard_restores_member_order_in_place_and_own_cache_only(self):
        rig,display,collections,_bones=self._display_fixture()
        def generic_recovery(_variant):
            collections[0].bones.reverse()
            display._FRAME_CACHE[17]=('callback cache',)
            display._FRAME_OWNER_REJECTIONS[17]=('callback refusal',)
        offset=NS(rollback_variant=Mock(side_effect=generic_recovery))
        with patch.dict(sys.modules,{PACKAGE+'.bone_collections':display,PACKAGE+'.control_pose_offset':offset}):
            saved=service._capture_display_checkpoint(rig)
            plan=dict(phase='published',variant=dict(rig=rig,staged_action=object()),display_checkpoint=saved)
            service.discard(plan)
        self.assertEqual(plan['phase'],'discarded')
        self.assertEqual(tuple(collections[0].bones),saved['collections'][0]['members'])
        self.assertEqual(collections[0].assign.call_count,3)
        self.assertFalse(collections[1].assign.called)
        self.assertEqual(display._FRAME_CACHE,{17:('old cache',),99:('foreign cache',)})
        self.assertEqual(display._FRAME_OWNER_REJECTIONS,{})

    def test_structural_display_edits_refuse_before_generic_rollback_and_cache_writes(self):
        for change in ('identity','order','name','parent','data','cache_table'):
            with self.subTest(change=change):
                rig,display,collections,_bones=self._display_fixture()
                offset=NS(rollback_variant=Mock())
                with patch.dict(sys.modules,{PACKAGE+'.bone_collections':display,PACKAGE+'.control_pose_offset':offset}):
                    saved=service._capture_display_checkpoint(rig)
                    plan=dict(phase='published',variant=dict(rig=rig,staged_action=object()),display_checkpoint=saved)
                    display._FRAME_CACHE[17]=('current cache',)
                    if change=='identity':
                        rig.data.collections_all=[NS(as_pointer=lambda:91),collections[1]]
                    elif change=='order':
                        rig.data.collections_all=list(reversed(collections))
                    elif change=='name':
                        collections[0].name='foreign rename'
                    elif change=='parent':
                        collections[1].parent=None
                    elif change=='data':
                        rig.data=NS(as_pointer=lambda:93,collections_all=collections)
                    else:
                        display._FRAME_CACHE={17:('current cache',)}
                    with self.assertRaisesRegex(ValueError,'recovery needs review'):
                        service.discard(plan)
                self.assertEqual(plan['phase'],'recovery_required')
                self.assertFalse(offset.rollback_variant.called)
                self.assertTrue(all(not c.assign.called and not c.unassign.called for c in collections))
                self.assertEqual(display._FRAME_CACHE[17],('current cache',))

    def test_post_recovery_member_set_failure_preserves_completed_recovery_without_guessing(self):
        rig,display,collections,_bones=self._display_fixture()
        def generic_recovery(variant):
            variant['phase']='rolled_back'  # The generic helper already removed its owned Action.
            collections[0].bones.reverse()
            collections[1].bones.clear()
            display._FRAME_CACHE[17]=('callback cache',)
        offset=NS(rollback_variant=Mock(side_effect=generic_recovery))
        with patch.dict(sys.modules,{PACKAGE+'.bone_collections':display,PACKAGE+'.control_pose_offset':offset}):
            saved=service._capture_display_checkpoint(rig)
            plan=dict(phase='published',variant=dict(rig=rig,staged_action=object()),display_checkpoint=saved)
            with self.assertRaisesRegex(ValueError,'member set'):
                service.discard(plan)
        self.assertEqual((plan['phase'],plan['variant']['phase']),('recovery_required','rolled_back'))
        self.assertTrue(all(not c.assign.called and not c.unassign.called for c in collections))
        self.assertEqual(tuple(collections[0].bones),tuple(reversed(saved['collections'][0]['members'])))
        self.assertEqual(display._FRAME_CACHE[17],('callback cache',))

    def test_failed_member_reordering_stays_recovery_required_without_stale_cache_restore(self):
        rig,display,collections,_bones=self._display_fixture()
        offset=NS(rollback_variant=Mock(side_effect=lambda _variant:collections[0].bones.reverse()))
        with patch.dict(sys.modules,{PACKAGE+'.bone_collections':display,PACKAGE+'.control_pose_offset':offset}):
            saved=service._capture_display_checkpoint(rig)
            plan=dict(phase='published',variant=dict(rig=rig,staged_action=object()),display_checkpoint=saved)
            collections[0].assign.side_effect=lambda bone:collections[0].bones.insert(0,bone)
            display._FRAME_CACHE[17]=('current cache',)
            with self.assertRaisesRegex(ValueError,'member order'):
                service.discard(plan)
        self.assertEqual(plan['phase'],'recovery_required')
        self.assertEqual(display._FRAME_CACHE[17],('current cache',))

    def test_prepared_only_discard_remains_inert_for_display_and_cache(self):
        offset=NS(rollback_variant=Mock())
        plan=dict(phase='prepared',variant={})
        with patch.dict(sys.modules,{PACKAGE+'.control_pose_offset':offset}), \
             patch.object(service,'_validate_display_checkpoint') as validate, \
             patch.object(service,'_restore_display_checkpoint') as restore:
            service.discard(plan)
        offset.rollback_variant.assert_called_once_with(plan['variant'])
        self.assertEqual(plan['phase'],'discarded')
        self.assertFalse(validate.called or restore.called)

    def _work(self):
        return {name:0 for name in ('ordinary','precise','refine','full_fallback','ordinary_accepted',
                                    'precise_accepted','refine_accepted','full_fallback_accepted',
                                    'limb_checks','full_body_checks')}

    def _staged_limb(self, errors, work=None):
        state = {'stage':'before', 'other_limb':'already accepted', 'toe':False}
        before = dict(state)
        events, precise_entry = [], []
        def match_ik(*args, **kwargs):
            precise = kwargs.get('precise',False)
            if precise:
                precise_entry.append(dict(state))
            state.update(stage='precise' if precise else 'ordinary',toe=False)
            events.append(state['stage'])
            return {'target','pole'}
        def toe(*args):
            state['toe']=True
            events.append('toe')
            return {'toe'}
        def refine(*args):
            state.update(stage='refine',toe=False)
            events.append('refine')
        def restore(_solver,saved):
            state.clear()
            state.update(saved)
            events.append('restore')
        def error(actual,expected):
            self.assertTrue(actual['toe'],'Local gate ran before matching Toe.')
            self.assertEqual(expected,('source-world',{'hand':'wanted'}))
            return dict(matrix=errors[actual['stage']],position=0.,rotation=0.,scale=0.)
        match=NS(_match_ik=Mock(side_effect=match_ik),_match_toe=Mock(side_effect=toe),
                 _refine_calibrated_reach=Mock(side_effect=refine))
        context=NS(view_layer=NS(update=Mock(side_effect=lambda:events.append('update'))))
        patches=[patch.dict(sys.modules,{PACKAGE+'.limb_ik_fk':match}),
                 patch.object(service,'_raw',side_effect=lambda *_args:dict(state)),
                 patch.object(service,'_restore_raw',side_effect=restore),
                 patch.object(service,'_pose',side_effect=lambda *_args:dict(state)),
                 patch.object(service,'_error',side_effect=error)]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            work=self._work() if work is None else work
            affected=service._match_limb(context,object(),{},object(),{'hand':'wanted'},
                                         'source-world',{'hand'},work)
        self.assertEqual(state['other_limb'],'already accepted')
        self.assertEqual(affected,{'target','pole','toe'})
        return work,events,precise_entry,before

    def test_ordinary_strict_pass_does_not_run_precise_or_reach_search(self):
        work,events,precise,_before=self._staged_limb({'ordinary':2.e-4})
        self.assertEqual(events,['ordinary','toe'])
        self.assertEqual((work['ordinary'],work['precise'],work['refine']),(1,0,0))
        self.assertEqual(work['ordinary_accepted'],1)
        self.assertFalse(precise)

    def test_precise_retry_restores_prelimb_state_and_uses_same_matrix_gate(self):
        work,events,precise,before=self._staged_limb({'ordinary':2.0001e-4,'precise':1.e-5})
        self.assertEqual(events,['ordinary','toe','restore','update','precise','toe'])
        self.assertEqual(precise,[before])
        self.assertEqual((work['ordinary'],work['precise'],work['refine']),(1,1,0))
        self.assertEqual(work['precise_accepted'],1)

    def test_reach_search_only_follows_an_actual_precise_pose_failure(self):
        work,events,_precise,_before=self._staged_limb({'ordinary':.001,'precise':.0003,'refine':.0001})
        self.assertEqual(events[-4:],['precise','toe','refine','toe'])
        self.assertEqual((work['ordinary'],work['precise'],work['refine']),(1,1,1))
        self.assertEqual((work['limb_checks'],work['refine_accepted']),(3,1))

    def test_local_failure_does_not_bypass_the_complete_body_gate(self):
        work,_events,_precise,_before=self._staged_limb({'ordinary':.001,'precise':.0003,'refine':.0004})
        self.assertEqual(work['refine_accepted'],0)
        self.assertEqual(work['limb_checks'],3)

    def test_search_work_remains_cumulative_across_recomputed_samples(self):
        work=self._work()
        self._staged_limb({'ordinary':.0001},work)
        self._staged_limb({'ordinary':.001,'precise':.0001},work)
        self.assertEqual((work['ordinary'],work['precise'],work['refine']),(2,1,0))
        self.assertEqual((work['ordinary_accepted'],work['precise_accepted'],work['limb_checks']),(1,1,3))

    def test_full_body_failure_gets_only_one_original_route_retry_from_source_inputs(self):
        from contextlib import ExitStack
        for final_matrix in (1.e-5,.001):
            with self.subTest(final_matrix=final_matrix):
                source_raw={'source-input':'same time sample'}
                context=NS(view_layer=NS(update=Mock()))
                inventory={'rigs':{'L':object(),'R':object()}}
                desired=('source-world',{'u':1,'l':2,'hand':3})
                match=NS(pose_names=lambda _entry:('u','l','hand'),
                    _match_ik=Mock(return_value={'target'}),_match_toe=Mock(return_value={'toe'}),
                    _refine_calibrated_reach=Mock())
                restore=Mock()
                with ExitStack() as stack:
                    stack.enter_context(patch.dict(sys.modules,{PACKAGE+'.limb_ik_fk':match}))
                    stack.enter_context(patch.object(service,'_match_limb',return_value={'ordinary-target'}))
                    stack.enter_context(patch.object(service,'_restore_raw',restore))
                    stack.enter_context(patch.object(service,'_pose',return_value='native actual'))
                    stack.enter_context(patch.object(service,'_error',side_effect=[
                        dict(matrix=.001,position=0.,rotation=0.,scale=0.),
                        dict(matrix=final_matrix,position=0.,rotation=0.,scale=0.)]))
                    work=self._work()
                    affected,errors=service._match_body(context,'solver',inventory,desired,source_raw,{'u','l','hand'},work)
                restore.assert_called_once_with('solver',source_raw)
                self.assertEqual(affected,{'target','toe'})
                self.assertEqual(errors['matrix'],final_matrix)
                self.assertEqual((work['full_fallback'],work['precise'],work['refine']),(1,2,2))
                self.assertEqual(work['full_body_checks'],2)
                self.assertEqual(work['full_fallback_accepted'],int(final_matrix<=2.e-4))
                self.assertTrue(all(call.kwargs=={'precise':True} for call in match._match_ik.call_args_list))

    def test_explicit_native_source_does_not_require_host_animdata(self):
        data = NS(library=None, pose_position='POSE', animation_data=None)
        rig = NS(type='ARMATURE', library=None, mode='OBJECT', data=data, parent=None,
                 constraints=[], modifiers=[], pose=NS(bones=[object()]), animation_data=None)
        action = NS(asset_data=None, frame_range=(1., 3.))
        inputs = NS(_handle=lambda *_args: 7, _curves=lambda *_args: [])
        limb = NS(_validate_inventory=lambda *_args: {'rigs': {}})
        with patch.object(service, '_api', return_value=(None, None, None, limb, inputs)):
            context=NS(scene=NS(unit_settings=NS(scale_length=1.)))
            result = service._source_plan(context, rig, action, 7, None, None, 1.)
        self.assertEqual(result['conversion_state'], 'NATIVE_FK')
        self.assertEqual(result['channels'], {})
        self.assertIsNone(result['metadata'])

    def test_missing_raw_inputs_skip_existing_and_driven_components(self):
        class PB:
            rotation_mode = 'QUATERNION'
            location = (0., 0., 0.)
            rotation_quaternion = (1., 0., 0., 0.)
            scale = (1., 1., 1.)
            def __init__(self, name):
                self.name = name
            def path_from_id(self, prop=None):
                prefix = 'pose.bones['+json.dumps(self.name)+']'
                return prefix + ('.'+prop if prop else '')
        upper, master, hand, pole = [PB(name) for name in ('upper', 'master', 'hand', 'pole')]
        original = NS(data_path=upper.path_from_id('location'), array_index=0, mute=False,
                      group=None, keyframe_points=[object()], sampled_points=[])
        rig = NS(pose=NS(bones={b.name:b for b in (upper, master, hand, pole)}))
        prop_path = master.path_from_id()+'["uniform_scale"]'
        driver = lambda path,index,variables: NS(data_path=path,array_index=index,mute=False,driver=NS(variables=variables))
        drivers = [driver(master.path_from_id('scale'), i,
                   [NS(type='SINGLE_PROP',targets=[NS(id=rig,data_path=prop_path)])]) for i in range(3)]
        rig.animation_data = NS(drivers=drivers)
        rig.path_resolve = lambda path: 1.0
        plan = dict(rig=rig, action=object(), slot=7, closure={'upper','master','hand','pole'}, secondary=[],
                    inventory={'rigs': {'arm': {'target': hand,'pole':pole}}},
                    drivers={(d.data_path,d.array_index) for d in drivers})
        inputs = NS(_curves=lambda *_args:[original])
        with patch.dict(sys.modules, {PACKAGE+'.animation_export_inputs':inputs}):
            missing = set(service._required_inputs(plan))
        self.assertNotIn((upper.path_from_id('location'),0),missing)
        self.assertIn((upper.path_from_id('location'),1),missing)
        self.assertIn((prop_path,0),missing)
        self.assertTrue(all((master.path_from_id('scale'),i) not in missing for i in range(3)))
        self.assertTrue(all(not key[0].startswith(hand.path_from_id()) for key in missing))

    def test_raw_clone_inputs_keep_integer_and_boolean_types(self):
        class Block(dict):
            rotation_mode = 'QUATERNION'
            location = (0.,0.,0.)
            rotation_euler = (0.,0.,0.)
            rotation_quaternion = (1.,0.,0.,0.)
            rotation_axis_angle = (0.,0.,1.,0.)
            scale = (1.,1.,1.)
            bl_rna = NS(properties=[])
            constraints = []
        rig, bone = Block(big=2**54+1, flag=True), Block(big=2**54+1, flag=False)
        rig.pose = NS(bones={'body':bone})
        inputs = NS(_POSE_SETTINGS=(),_settings=lambda *_args:{})
        with patch.dict(sys.modules, {PACKAGE+'.animation_export_inputs':inputs}):
            result = service._raw(rig, {'body'})
        self.assertIs(type(result['object']['properties']['big']),int)
        self.assertEqual(result['pose']['body']['properties']['big'],2**54+1)
        self.assertIs(result['pose']['body']['properties']['flag'],False)

    def test_solver_inventory_never_resolves_removed_display_records(self):
        body = (NS(name='native body'), object(), {'role': 'IK'})
        display = (NS(name='pole guide'), object(), {'role': 'POLE_DISPLAY_TRACK'})
        inventory = dict(records=[body, display], master_records=[],
                         rigs={'arm': {'entries': [body, display], 'target': 'preserved metadata'}})
        plan = dict(inventory=inventory, closure={'native body'}, rig=object())
        with patch.object(service, '_remap', side_effect=lambda value, *_args: value):
            result = service._solver_inventory(plan, object())
        self.assertEqual(result['records'], [body])
        self.assertEqual(result['rigs']['arm']['entries'], [body])
        self.assertEqual(inventory['records'], [body, display])

    def test_driver_self_id_cannot_hide_external_pointer_path(self):
        rig = NS(data=object(), pose=NS(bones={'control': object()}))
        target = NS(id=rig, bone_target='', data_path='pose.bones["control"]["ik_fk"]')
        curve = NS(driver=NS(use_self=False, type='SCRIPTED', is_simple_expression=True,
                             variables=[NS(targets=[target])]))
        self.assertEqual(service._driver_dependencies(rig, curve), {'control'})
        target.data_path = '["external_object"].location[0]'
        with self.assertRaisesRegex(ValueError, 'indirect ID path'):
            service._driver_dependencies(rig, curve)

    def test_secondary_spline_cannot_solve_native_parent_outside_its_branch(self):
        class ID:
            pass
        class Bones(dict):
            def __iter__(self):
                return iter(self.values())
        rig = ID()
        rig.data, rig.animation_data = ID(), None
        body = NS(name='body', parent=None, constraints=[])
        secondary = NS(name='dress', parent=body, constraints=[])
        rig.pose = NS(bones=Bones(body=body, dress=secondary))
        secondary.constraints.append(NS(type='SPLINE_IK', chain_count=2,
            bl_rna=NS(properties=[NS(identifier='target', type='POINTER')]),
            target=ID(), subtarget='', pole_subtarget=''))
        with patch.dict(sys.modules, {'bpy': NS(types=NS(ID=ID))}):
            with self.assertRaisesRegex(ValueError, 'external constraint input affects the body'):
                service._closure(rig, {'rigs': {}}, {'dress'}, set())

    def test_publication_evaluates_mode_and_synchronizes_display_without_matching(self):
        self._publication_case(False)

    def test_publication_display_failure_restores_cache_after_author_rollback(self):
        self._publication_case(True)

    def _publication_case(self, fail):
        mode = ['FK']
        rig = NS(as_pointer=lambda: 17,data=NS(as_pointer=lambda:29,collections_all=[]))
        context = NS(view_layer=NS(update=Mock()),scene=NS(unit_settings=NS(scale_length=1.)))
        entry = object()
        action, payload = object(), object()
        old_cache, old_refusal = ('saved cache',), ('saved refusal',)
        display = NS(_FRAME_CACHE={17: old_cache}, _FRAME_OWNER_REJECTIONS={17: old_refusal})
        def sync(*args, **kwargs):
            display._FRAME_CACHE[17] = ('new cache',)
            display._FRAME_OWNER_REJECTIONS.pop(17, None)
            if fail:
                raise RuntimeError('display failure')
        display.sync_limb_display = Mock(side_effect=sync)
        def publish(*args, **kwargs):
            mode[0] = 'IK'
            return {}
        offset = NS(publish_variant=Mock(side_effect=publish), rollback_variant=Mock())
        limb = NS(_validate_inventory=Mock(return_value={'rigs': {'arm': entry}}))
        match = NS(mode_for_rig=lambda rig, entry: mode[0])
        plan = dict(phase='built', source={'inventory': {'rigs': {'arm': entry}},'context':context,
                                          'quality':'EDITABLE','playback_policy':self._policy()}, result=payload,
                    variant={'rig': rig, 'context': context, 'staged_action': action, 'saved': NS(status='')})
        # The result body is irrelevant to the rollback case, but publication
        # success returns the exact metadata/diagnostics emitted by the builder.
        payload = {'metadata': {}, 'diagnostics': {}}
        plan['result'] = payload
        built = {'action': action, 'result': payload}
        modules = {PACKAGE+'.'+name: module for name, module in
                   [('control_pose_offset', offset), ('bone_collections', display),
                    ('limb_ik', limb), ('limb_ik_fk', match)]}
        with patch.dict(sys.modules, modules):
            plan['display_checkpoint']=service._capture_display_checkpoint(rig)
            if fail:
                with self.assertRaisesRegex(RuntimeError, 'display failure'):
                    service.publish(plan, built)
                offset.rollback_variant.assert_called_once_with(plan['variant'])
                self.assertIs(display._FRAME_CACHE[17], old_cache)
                self.assertIs(display._FRAME_OWNER_REJECTIONS[17], old_refusal)
                self.assertEqual(plan['phase'], 'discarded')
            else:
                self.assertEqual(service.publish(plan, built)['conversion_state'], 'CONTROLLER')
                self.assertEqual(plan['phase'], 'published')
                self.assertFalse(offset.rollback_variant.called)
        display.sync_limb_display.assert_called_once_with(rig, limb._validate_inventory.return_value, force_flags=True)
        self.assertTrue(context.view_layer.update.called)

    def test_unrelated_secondary_external_graph_is_cut_but_body_backread_refuses(self):
        class ID:
            pass
        class Bones(dict):
            def __iter__(self):
                return iter(self.values())
        rig = ID()
        rig.data, rig.animation_data = ID(), None
        body = NS(name='body', parent=None, constraints=[])
        secondary = NS(name='verified dress', parent=body, constraints=[])
        rig.pose = NS(bones=Bones(body=body, **{'verified dress': secondary}))
        pointer = NS(identifier='target', type='POINTER')
        foreign = ID()
        secondary.constraints.append(NS(type='COPY_ROTATION', bl_rna=NS(properties=[pointer]),
                                        target=foreign, subtarget='', pole_subtarget=''))
        fake_bpy = NS(types=NS(ID=ID))
        with patch.dict(sys.modules, {'bpy': fake_bpy}):
            native, closure, drivers = service._closure(rig, {'rigs': {}}, {'verified dress'}, set())
            self.assertEqual(native, ('body',))
            self.assertEqual(closure, frozenset({'body'}))
            body.constraints.append(NS(type='COPY_ROTATION', bl_rna=NS(properties=[pointer]),
                                       target=rig, subtarget='verified dress', pole_subtarget=''))
            with self.assertRaisesRegex(ValueError, 'external constraint input affects the body'):
                service._closure(rig, {'rigs': {}}, {'verified dress'}, set())

    def test_status_is_metadata_only_and_source_is_never_editable(self):
        context, action = fixture()
        with patch.object(service, '_api', side_effect=AssertionError('Panel loaded full services.')):
            self.assertEqual(service.status(context)['state'], 'CANDIDATE')
            context.item.side = 'SOURCE'
            value = service.status(context)
            self.assertFalse(value['can_convert'])
            self.assertEqual(value['message'], 'Select Custom to convert.')
            self.assertEqual(action, {})

    def test_status_requires_exact_active_action_and_slot(self):
        context, action = fixture()
        context.item.rig.animation_data.action = Action()
        self.assertEqual(service.status(context)['state'], 'UNAVAILABLE')
        context.item.rig.animation_data.action = action
        context.item.rig.animation_data.action_slot.handle = 8
        self.assertEqual(service.status(context)['state'], 'UNAVAILABLE')

    def test_bounded_metadata_and_escaped_native_bone_paths(self):
        context, action = fixture()
        action[service.RECORD] = json.dumps(dict(schema=service.SCHEMA, frame_start=2.5,
                                               frame_end=41., limbs=['ARM.L', 'LEG.R']))
        value = service.status(context)
        self.assertEqual((value['state'], value['frame_start'], value['frame_end']), ('CONTROLLER', 2.5, 41.))
        action[service.RECORD] = ' ' * (service.MAX_RECORD + 1)
        self.assertEqual(service.status(context)['state'], 'CANDIDATE')
        self.assertEqual(service._bone_name('pose.bones["arm\\\".L"].location'), 'arm".L')
        self.assertEqual(service._bone_name('data.bones["arm.L"]["flag"]'), 'arm.L')

    def test_whole_clip_retains_authored_subframes_and_rejects_partial_interval(self):
        action = NS(frame_range=(1., 3.))
        curves = [NS(keyframe_points=[NS(co=NS(x=1.25)), NS(co=NS(x=2.75))])]
        self.assertEqual(service._times(action, curves, None, None, 1.), (1., 1.25, 2., 2.75, 3.))
        with self.assertRaisesRegex(ValueError, 'complete clip'):
            service._times(action, curves, 2., 3., 1.)

    def test_writer_refuses_source_bound_and_foreign_actions_before_writes(self):
        source = Action()
        built = dict(conversion_state='CONTROLLER', source_action=source, source_slot=7,
                     frame_times=(1., 2.), channels={('pose.bones["hand"].location', 0): [0., 1.]},
                     metadata={'schema': service.SCHEMA}, diagnostics={})
        writer = Mock(side_effect=AssertionError('Writer ran before ownership refusal.'))
        with patch.object(service, '_write_channels', writer):
            with self.assertRaisesRegex(ValueError, 'independent Action copy'):
                service.apply_to_action(source, 7, built)
            target = Action()
            target.users = 1
            with self.assertRaisesRegex(ValueError, 'independent Action copy'):
                service.apply_to_action(target, 7, built)
            target.users = 0
            with self.assertRaisesRegex(ValueError, 'slot or metadata'):
                service.apply_to_action(target, 8, built)
        self.assertEqual(source, {})
        self.assertFalse(writer.called)

    def test_native_fk_is_not_marked_as_controller_animation(self):
        source, target = Action(), Action()
        target[service.RECORD] = 'old copied controller marker'
        built = dict(conversion_state='NATIVE_FK', source_action=source,
                     diagnostics={'reason': 'No installed controls.'})
        result = service.apply_to_action(target, 7, built)
        self.assertEqual(result['conversion_state'], 'NATIVE_FK')
        self.assertIsNone(result['metadata'])
        self.assertNotIn(service.RECORD, target)
        self.assertEqual(source, {})

    def test_writer_changes_only_owned_metadata_and_explicit_output_overlay(self):
        source, target = Action(), Action()
        source['foreign'] = 'source metadata'
        target.update(foreign='copied metadata', export_base='old owned-copy schema', export_count=9)
        built = dict(conversion_state='CONTROLLER', source_action=source, source_slot=7,
                     frame_times=(1., 2.), channels={('pose.bones["hand"].location', 0): [0., 1.]},
                     metadata={'schema': service.SCHEMA}, diagnostics={}, retained_indices={})
        inputs = NS(RECORD='export_base', COUNT='export_count')
        writer = Mock()
        with patch.dict(sys.modules, {PACKAGE+'.animation_export_inputs': inputs}), patch.object(service, '_write_channels', writer):
            service.apply_to_action(target, 7, built)
        writer.assert_called_once_with(target, 7, built, {})
        self.assertEqual(target['foreign'], 'copied metadata')
        self.assertNotIn('export_base', target)
        self.assertNotIn('export_count', target)
        self.assertEqual(json.loads(target[service.RECORD]), {'schema': service.SCHEMA})
        self.assertEqual(source, {'foreign': 'source metadata'})

    def test_pole_phase_driver_refuses_before_controller_changes(self):
        path = 'pose.bones["lower"].constraints["owned"].pole_angle'
        constraint = NS(pole_angle=.7, path_from_id=lambda _field: path)
        entry = {'pole': NS(name='Pole')}
        match = NS(_fixed_pole_constraint=lambda _entry: constraint,
                   _update=Mock(), _match_pole_plane=Mock(), _match_toe=Mock())
        rig = NS(animation_data=NS(drivers=[NS(data_path=path, mute=False)]))
        references = {}
        with patch.dict(sys.modules, {PACKAGE+'.limb_ik_fk': match}):
            with self.assertRaisesRegex(ValueError, 'Pole angle driver'):
                service._stabilize_pole_phase(object(), rig, {'rigs': {'R': entry}},
                                             (None, {}), references)
        self.assertEqual(references, {})
        self.assertEqual(constraint.pole_angle, .7)
        self.assertFalse(match._update.called or match._match_pole_plane.called or match._match_toe.called)

    def test_pole_phase_keeps_independent_first_references_on_retries(self):
        constraints = {key: NS(pole_angle=value, path_from_id=lambda _field: 'owned-angle')
                       for key, value in (('L', .7), ('R', 3.1))}
        entries = {key: {'key': key, 'pole': NS(name='Pole'+key)} for key in constraints}
        events = []
        match = NS(_fixed_pole_constraint=lambda entry: constraints[entry['key']],
                   pose_names=lambda _entry: ('lower',),
                   _update=lambda *_args: events.append('update'),
                   _match_pole_plane=lambda _context, _rig, entry, wanted, **kwargs:
                       events.append(('plane', entry['key'], wanted, kwargs)),
                   _match_toe=lambda _context, _rig, entry, _wanted: {'Toe'+entry['key']})
        rig = NS(animation_data=NS(drivers=[NS(data_path='unrelated', mute=False),
                                         NS(data_path='owned-angle', mute=True)]))
        references = {}
        with patch.dict(sys.modules, {PACKAGE+'.limb_ik_fk': match}):
            first = service._stabilize_pole_phase(object(), rig, {'rigs': entries},
                                                 (None, {'lower': 'wanted'}), references)
            self.assertEqual(first, {'PoleL', 'PoleR'})
            self.assertEqual(events, [])
            for requested in ((.2, -3.1), (.4, 3.0)):
                for key, angle in zip(('L', 'R'), requested):
                    constraints[key].pole_angle = angle
                changed = service._stabilize_pole_phase(object(), rig, {'rigs': entries},
                                                       (None, {'lower': 'wanted'}), references)
                self.assertEqual(changed, {'PoleL', 'PoleR', 'ToeL', 'ToeR'})
                self.assertEqual({key: con.pole_angle for key, con in constraints.items()},
                                 {'L': .7, 'R': 3.1})
        self.assertEqual(references, {'L': .7, 'R': 3.1})
        self.assertEqual(events, ['update', ('plane', 'L', {'lower': 'wanted'}, {'precise': True}),
                                  'update', ('plane', 'R', {'lower': 'wanted'}, {'precise': True})] * 2)

    def test_custom_conversion_cannot_begin_from_source(self):
        context, action = fixture()
        context.item.side = 'SOURCE'
        offset = NS(prepare_variant=Mock(side_effect=AssertionError('Source entered a writer transaction.')))
        api = NS(worklist=worklist)
        with patch.object(service, '_api', return_value=(offset, api, None, None, None)):
            with self.assertRaisesRegex(ValueError, 'Select Custom'):
                service.prepare(context, context.item.rig)
        self.assertFalse(offset.prepare_variant.called)
        self.assertEqual(action, {})


if __name__ == '__main__':
    unittest.main()
