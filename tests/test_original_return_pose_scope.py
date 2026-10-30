"""Settled Original pose distinguishes actual edits from pre-existing bake noise."""
import ast
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'addons/character_designer/body_original_mode.py'
spec=importlib.util.spec_from_file_location('_original_pose_scope_display_fixture',
                                           ROOT/'tests/test_original_return_saved_display.py')
fixture=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def matrix(x):
    return [[1.,0.,0.,float(x)],[0.,1.,0.,0.],[0.,0.,1.,0.],[0.,0.,0.,1.]]


def difference(a,b):
    return max(abs(x-y) for ar,br in zip(a,b) for x,y in zip(ar,br))


def finite(value):
    return len(value)==4 and all(len(row)==4 and all(math.isfinite(x) for x in row) for row in value)


class OriginalPoseScopeTests(unittest.TestCase):
    invoke=fixture.OriginalSavedDisplayTests.invoke

    def setUp(self):
        fixture.MODE_SOURCE=SOURCE
        fixture.OriginalSavedDisplayTests.setUp(self)
        self.initial_angles={'shin.L':-.00447289599,'shin.R':-3.13711977}
        self.angles={name:NS(type='IK',pole_angle=value) for name,value in self.initial_angles.items()}
        for name,con in self.angles.items():
            self.rig.pose.bones[name]=NS(name=name,matrix=None,constraints=[con])
        self.control={'native':matrix(10.),'shin.L':matrix(20.),'shin.R':matrix(30.)}
        self.entered={'native':matrix(10.),'shin.L':matrix(20.000001),'shin.R':matrix(30.000001)}
        self.desired_matrices=deepcopy(self.entered)
        self.desired_matrices['native']=matrix(10.04)
        self.saved['channels']={name:0 for name in self.control}
        self.saved['entered_channels']={name:1 for name in self.control}
        self.saved['entered_pose']=deepcopy(self.entered)
        self.rig.channels=dict(self.saved['entered_channels'],native=2)
        for name,pb in self.rig.pose.bones.items():pb.matrix=deepcopy(self.desired_matrices[name])

        def restore(rig,channels):
            rig.channels=deepcopy(channels)
            matrices=self.control if channels==self.saved['channels'] else self.desired_matrices
            for name,pb in rig.pose.bones.items():pb.matrix=deepcopy(matrices[name])

        def match(context,rig,desired,changed,**kwargs):
            # The real matcher rematches an entire limb when its source is in
            # changed. Its writable angle is the native regression witness.
            for name in self.angles:
                if name in changed:self.angles[name].pole_angle+=.033
            for name in changed:rig.pose.bones[name].matrix=deepcopy(desired[name])

        def verify(rig,desired):
            if max(difference(desired[name],rig.pose.bones[name].matrix) for name in desired)>4e-4:
                raise ValueError('Native pose verification failed')

        self.match_pose=Mock(side_effect=match)
        self.restore=Mock(side_effect=restore)
        self.ns.update(_pose=lambda *args:deepcopy(self.desired_matrices),_restore_channels=self.restore,
            _verify=Mock(side_effect=verify),math=math)
        self.ns['poses']._difference=difference
        self.ns['poses']._match=self.match_pose
        self.ns['limb_ik']._matrix_is_finite=finite
        nodes=[n for n in ast.parse(SOURCE.read_text(encoding='utf-8-sig')).body
               if isinstance(n,ast.FunctionDef) and n.name=='_session_pose_edits']
        if nodes:
            exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.ns)

    def angle_values(self):return {name:con.pole_angle for name,con in self.angles.items()}

    def test_finger_edit_does_not_rematch_untouched_legs_to_remove_bake_noise(self):
        self.assertTrue(self.invoke())
        self.assertEqual(self.match_pose.call_args.args[3],{'native'})
        self.assertEqual(self.angle_values(),self.initial_angles)
        self.assertTrue(self.match_pose.call_args.kwargs['preserve_modes'])
        self.ns['_verify'].assert_called_once()

    def test_parent_change_propagates_actual_native_motion_to_both_legs(self):
        self.desired_matrices={name:matrix(rows[0][3]+.02) for name,rows in self.entered.items()}
        self.assertTrue(self.invoke())
        self.assertEqual(self.match_pose.call_args.args[3],set(self.control))
        self.assertNotEqual(self.angle_values(),self.initial_angles)

    def test_constraint_dependency_moves_leg_even_when_its_local_channels_are_unchanged(self):
        self.rig.channels=deepcopy(self.saved['entered_channels'])
        self.desired_matrices=deepcopy(self.entered)
        self.desired_matrices['shin.L']=matrix(20.02)
        self.assertTrue(self.invoke())
        self.assertEqual(self.match_pose.call_args.args[3],{'shin.L'})
        self.assertEqual(self.angles['shin.R'].pole_angle,self.initial_angles['shin.R'])

    def test_unedited_session_does_not_rematch_its_recorded_bake_noise(self):
        self.rig.channels=deepcopy(self.saved['entered_channels'])
        self.desired_matrices=deepcopy(self.entered)
        self.assertTrue(self.invoke())
        self.match_pose.assert_not_called()
        self.assertEqual(self.angle_values(),self.initial_angles)

    def test_rest_plan_keeps_its_forced_leg_and_existing_precision_route(self):
        self.rig.channels=deepcopy(self.saved['entered_channels'])
        self.desired_matrices=deepcopy(self.entered)
        self.desired_matrices['shin.L']=matrix(20.02)
        self.ns['_validate_session_rest']=Mock(side_effect=ValueError('Native Rest edited'))
        plan=dict(rest={'native':{}},keys=(('LEG','L'),))
        self.resync.prepare.return_value=plan
        self.resync.apply.return_value={'shin.L'}
        self.assertTrue(self.invoke(legacy=True))
        self.assertEqual(self.match_pose.call_args.args[3],{'shin.L'})
        self.assertEqual(self.match_pose.call_args.kwargs['precise_limbs'],(('LEG','L'),))

    def test_old_session_without_settled_pose_keeps_conservative_legacy_matching(self):
        del self.saved['entered_pose']
        self.assertTrue(self.invoke())
        self.assertEqual(self.match_pose.call_args.args[3],set(self.control))

    def test_malformed_entry_pose_refuses_before_channel_or_angle_writes(self):
        bad_records=[None,{},dict(self.entered,foreign=matrix(0.)),
                     dict(self.entered,native=[[1.]]),
                     dict(self.entered,native=matrix(float('nan'))),
                     dict(self.entered,native=[[True,0.,0.,0.]]*4)]
        for entered in bad_records:
            with self.subTest(entered=entered):
                self.saved['entered_pose']=entered
                before_channels=deepcopy(self.rig.channels)
                with self.assertRaisesRegex(ValueError,'entry pose is invalid'):self.invoke()
                self.assertEqual(self.rig.channels,before_channels)
                self.assertEqual(self.angle_values(),self.initial_angles)
                self.restore.assert_not_called()
                self.match_pose.assert_not_called()
                self.assertIn(fixture.SESSION,self.rig)

    def test_nonfinite_current_pose_does_not_enter_restore_or_matching(self):
        self.desired_matrices['native']=matrix(float('inf'))
        with self.assertRaisesRegex(ValueError,'entry pose is invalid'):self.invoke()
        self.restore.assert_not_called()
        self.assertEqual(self.angle_values(),self.initial_angles)

    def test_post_match_failure_restores_original_session_channels_and_all_pole_angles(self):
        self.desired_matrices['shin.L']=matrix(20.02)
        before_channels=deepcopy(self.rig.channels)
        self.ns['_verify'].side_effect=ValueError('Failure after actual matching')
        with self.assertRaisesRegex(ValueError,'after actual matching'):self.invoke()
        self.assertNotEqual(self.match_pose.call_args.args[3],set())
        self.assertEqual(self.angle_values(),self.initial_angles)
        self.assertEqual(self.rig.channels,before_channels)
        self.assertIn(fixture.SESSION,self.rig)
        self.assertIn(fixture.POSE_REFS,self.rig)
        self.restored_owner.assert_called_once_with(self.cap)


if __name__=='__main__':unittest.main()
