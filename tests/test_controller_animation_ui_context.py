"""Bounded workspace/job presentation derivative; no native authoring calls.

The inherited UI fixture adds only the real animation._job field. Its prior
bytes are preserved under X/context_guard01_prior for the earlier receipts.
"""
import sys
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest

import test_controller_animation_ui as fixture


class Rig(NS):
    def __contains__(self,key):
        return key in self.markers


class WorkspaceContextUI(fixture.ControllerUI):
    def setUp(self):
        super().setUp()
        self.rig=Rig(**vars(self.rig), data=NS(pose_position='POSE'), markers={})
        self.item.rig=self.rig
        self.context.object=self.rig
        self.context.scene.objects={self.rig.name:self.rig}
        self.context.view_layer.objects={self.rig.name:self.rig}
        self.target.id_data=self.rig

    def refused_workspace(self,message):
        self.backend.status.return_value=dict(state='CONTROLLER',can_convert=True,has_record=True)
        self.action[self.ui.RECORD]='retained marker hint'
        layout=fixture.Layout()
        with patch.object(self.ui,'_backend',side_effect=AssertionError('workspace imported converter')), \
             patch.object(self.ui,'_move_backend',side_effect=AssertionError('workspace imported move')):
            self.ui.draw_controller_animation(layout,self.context)
            self.assertTrue(all(not event[4] for event in layout.events if event[0]=='operator'))
            self.assertIn(message,[event[1]['text'] for event in layout.events if event[0]=='label'])
            self.assertFalse(self.ui.CHARACTERDESIGNER_OT_make_controller_animation.poll(self.context))
            self.assertFalse(self.ui.CHARACTERDESIGNER_OT_apply_controller_move_across_clip.poll(self.context))
            self.assertEqual(self.operator().execute(self.context),{'CANCELLED'})
            self.assertEqual(self.move_operator().execute(self.context),{'CANCELLED'})
        self.backend.status.assert_not_called()
        self.backend.convert.assert_not_called()

    def test_native_rest_and_natural_tokens_show_Return_to_Animation(self):
        message='Return to Animation before editing controller motion.'
        self.rig.data.pose_position='REST'
        self.refused_workspace(message)
        self.rig.data.pose_position='POSE'
        for key in ('character_designer_base_pose_test_v1','character_designer_natural_pose_test_v1'):
            with self.subTest(marker=key):
                self.rig.markers[key]=None  # Invalid tokens still block authoring.
                self.refused_workspace(message)
                self.rig.markers.clear()

    def test_custom_or_active_BasePose_draft_show_Return_to_Animation(self):
        message='Return to Animation before editing controller motion.'
        for key in ('character_designer_base_pose_test_session_v1','character_designer_base_pose_test_refs_v1'):
            with self.subTest(marker=key):
                self.action[key]='unparsed retained recovery'
                self.refused_workspace(message)
                del self.action[key]
        draft=fixture.Action('Visible Base Pose Draft')
        draft['character_designer_base_pose_test_session_v1']='unparsed draft'
        self.rig.animation_data.action=draft
        self.refused_workspace(message)

    def test_Original_marker_shows_Return_to_Controls(self):
        self.rig.markers['character_designer_body_original_mode_v1']=None
        self.refused_workspace('Return to Controls before editing controller motion.')

    def test_draw_never_calls_importing_idle_or_mutating_scan_service(self):
        animation=sys.modules[self.package_name+'.animation']
        animation._link_idle=Mock(side_effect=AssertionError('draw called importing idle service'))
        layout=fixture.Layout()
        self.ui.draw_controller_animation(layout,self.context)
        self.assertTrue(self.ui.CHARACTERDESIGNER_OT_make_controller_animation.poll(self.context))
        animation._link_idle.assert_not_called()
        self.backend.status.assert_called_once_with(self.context)

    def test_loaded_backend_cannot_import_an_uncached_worklist_during_draw(self):
        sys.modules.pop(self.package_name+'.animation_worklist')
        self.action[self.ui.RECORD]='bounded retained marker hint'
        layout=fixture.Layout()
        self.ui.draw_controller_animation(layout,self.context)
        self.backend.status.assert_not_called()
        self.backend.convert.assert_not_called()
        self.assertIn('Controller Animation',[e[1]['text'] for e in layout.events if e[0]=='label'])

    def test_loaded_jobs_are_read_only_and_busy_blocks_both_entries(self):
        self.action[self.ui.RECORD]='controller hint'
        for short,field in (('animation_export','_ACTIVE_JOB'),('unity_export','_ACTIVE_JOB'),
                            ('animation_worklist_ui','_SCAN_PENDING'),('animation_worklist_ui','_LIST_SYNC_PENDING')):
            with self.subTest(module=short,field=field):
                module=ModuleType(self.package_name+'.'+short)
                marker={'armed':True};setattr(module,field,marker)
                module.scan_pending=Mock(side_effect=AssertionError('draw mutates stale scan guard'))
                with patch.dict(sys.modules,{module.__name__:module}):
                    self.assertFalse(self.ui._idle())
                    self.assertFalse(self.ui.CHARACTERDESIGNER_OT_make_controller_animation.poll(self.context))
                    self.assertFalse(self.ui.CHARACTERDESIGNER_OT_apply_controller_move_across_clip.poll(self.context))
                self.assertIs(getattr(module,field),marker)
                module.scan_pending.assert_not_called()

    def test_collection_busy_and_unknown_batch_fail_closed_without_service_calls(self):
        module=ModuleType(self.package_name+'.animation_worklist_collection')
        module.running=Mock(side_effect=AssertionError('draw called a collection service'))
        with patch.dict(sys.modules,{module.__name__:module}):
            for batch in ({'queue':NS(busy=True)}, {'queue':None}, object()):
                module._batch=batch
                self.assertFalse(self.ui._idle())
                self.assertIs(module._batch,batch)
            module._batch={'queue':NS(busy=False)}
            self.assertTrue(self.ui._idle())
        module.running.assert_not_called()


if __name__=='__main__':
    unittest.main()
