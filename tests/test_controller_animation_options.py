"""User budgets and reduction preferences cannot relax native motion gates."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]/'addons'/'character_designer'
PACKAGE = '_controller_option_tests'
package = types.ModuleType(PACKAGE); package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package


def load(name):
    spec = importlib.util.spec_from_file_location(PACKAGE+'.'+name, ROOT/(name+'.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


service = load('controller_animation')
sampling = load('controller_animation_sampling')
NS = types.SimpleNamespace


class ControllerOptionsTest(unittest.TestCase):
    def context(self):
        return NS(scene=NS(unit_settings=NS(scale_length=1.)))

    def test_stricter_world_budgets_leave_native_and_shape_gates_exact(self):
        context = self.context()
        default = service._quality_policy(context,'EDITABLE')
        strict = service._quality_policy(context,'EDITABLE',position_tolerance_meters=.0005,
                                         rotation_tolerance_radians=.002)
        self.assertEqual({key:value for key,value in strict.items() if key not in
            {'position_tolerance_meters','rotation_tolerance_radians'}},
            {key:value for key,value in default.items() if key not in
             {'position_tolerance_meters','rotation_tolerance_radians'}})
        errors=dict(position_meters=.00075,rotation_radians=.003,scale_relative=0.,shape_relative=0.)
        self.assertTrue(service._playback_acceptable(errors,default))
        self.assertFalse(service._playback_acceptable(errors,strict))
        self.assertEqual(service.MATRIX_TOLERANCE,2.e-4)
        self.assertEqual(strict['matched_sample_gate'],dict(matrix=2.e-4,position=2.e-4,rotation=2.e-3,scale=2.e-4))

    def test_looser_nonfinite_zero_and_boolean_budgets_refuse(self):
        for key,values in (('position_tolerance_meters',(0.,-.1,.001000001,float('inf'),float('nan'),True,10**400)),
                           ('rotation_tolerance_radians',(0.,-.1,.004364,float('inf'),float('nan'),False,10**400))):
            for value in values:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    service._conversion_options(**{key:value})
        for value in (0,1,None,'yes'):
            with self.subTest(auto=value),self.assertRaises(ValueError):
                service._conversion_options(auto_reduce=value)

    def test_strict_native_quality_cannot_silently_ignore_custom_world_budget(self):
        policy=service._quality_policy(self.context(),'STRICT')
        self.assertEqual(policy['native_matrix_tolerance'],2.e-4)
        self.assertEqual(policy['rotation_tolerance_radians'],2.e-3)
        with self.assertRaisesRegex(ValueError,'apply to EDITABLE'):
            service._quality_policy(self.context(),'STRICT',position_tolerance_meters=.0005)

    def test_default_call_shapes_and_nondefault_forwarding(self):
        with patch.object(service,'convert_current_action',return_value='done') as convert:
            self.assertEqual(service.convert('ctx','rig'),'done')
            convert.assert_called_once_with('ctx','rig',activate=True,quality='EDITABLE')
        options=dict(auto_reduce=False,position_tolerance_meters=.0005,rotation_tolerance_radians=.002)
        with patch.object(service,'convert_current_action',return_value='done') as convert:
            self.assertEqual(service.convert('ctx','rig',**options),'done')
            convert.assert_called_once_with('ctx','rig',activate=True,quality='EDITABLE',**options)
        with patch.object(service,'prepare',return_value={'phase':'prepared'}) as prepare, \
             patch.object(service,'build',return_value='built'),patch.object(service,'publish',return_value='new'):
            self.assertEqual(service.convert_current_action('ctx','rig',**options),'new')
            prepare.assert_called_once_with('ctx','rig',frame_start=None,frame_end=None,quality='EDITABLE',**options)

    def test_prepare_build_preserve_custom_options_and_policy(self):
        context=self.context();rig=object();action=object()
        options=service._conversion_options(False,.0005,.002)
        source=dict(conversion_state='CONTROLLER',context=context,rig=rig,action=action,slot=7,
            quality='EDITABLE',conversion_options=options,
            playback_policy=service._quality_policy(context,'EDITABLE',position_tolerance_meters=.0005,
                                                     rotation_tolerance_radians=.002))
        variant=dict(original=action,slot=7,frame=1.)
        offset=NS(prepare_variant=Mock(return_value=variant),_variant_owner=Mock(),new_variant_action=Mock(return_value='new'))
        api=NS(worklist=NS(current=lambda _:NS(side='CUSTOM',rig=rig)))
        with patch.object(service,'_api',return_value=(offset,api,None,None,None)), \
             patch.object(service,'_source_plan',return_value=source) as planned, \
             patch.object(service,'_pending_keyed_inputs',return_value=()), \
             patch.object(service,'_capture_display_checkpoint',return_value='checkpoint'), \
             patch.object(service,'build_controller_channels',return_value='channels') as build, \
             patch.object(service,'apply_to_action'):
            plan=service.prepare(context,**options)
            self.assertEqual(planned.call_args.kwargs,dict(quality='EDITABLE',**options))
            self.assertEqual(service.build(plan),dict(action='new',result='channels'))
            build.assert_called_once_with(context,rig,action,7,quality='EDITABLE',**options)

    def test_inconsistent_budget_refuses_before_builder_or_publish_writes(self):
        context=self.context()
        source=dict(context=context,quality='EDITABLE',conversion_options=service._conversion_options(False,.0005,.002),
                    playback_policy=service._quality_policy(context,'EDITABLE'))
        offset=NS(_variant_owner=Mock())
        with patch.object(service,'_api',return_value=(offset,None,None,None,None)), \
             patch.object(service,'build_controller_channels') as builder:
            with self.assertRaises(ValueError):
                service.build(dict(phase='prepared',variant={},source=source))
            builder.assert_not_called()
        value=object();payload=object()
        plan=dict(phase='built',variant={'staged_action':value},result=payload,source=source)
        with self.assertRaises(ValueError):
            service.publish(plan,dict(action=value,result=payload))

    def test_invalid_options_refuse_before_capture_api(self):
        with patch.object(service,'_api') as api:
            with self.assertRaises(ValueError):
                service.prepare(self.context(),position_tolerance_meters=.002)
            api.assert_not_called()

    def test_reduction_off_still_writes_only_legal_dense_subset(self):
        times=(1.,2.,3.,30.,30.000001907348633)
        data={('pose.bones["CTRL_hand_IK.L"].location',axis):[float(i) for i in range(5)] for axis in range(3)}
        reducer=NS(compress=Mock(side_effect=AssertionError('Disabled reducer executed.')))
        proposal=sampling.compress_legal(data,times,reducer,auto_reduce=False)
        reducer.compress.assert_not_called()
        self.assertFalse(proposal['compression_attempted'])
        self.assertEqual(proposal['indices'],proposal['fallback_indices'])
        self.assertEqual(proposal['legal_indices'],(0,1,2,3))
        self.assertEqual(proposal['validation_sample_count'],5)
        self.assertTrue(proposal['native_verification_required'])
        self.assertTrue(all(4 not in indices for indices in proposal['indices'].values()))


if __name__=='__main__':
    unittest.main()
