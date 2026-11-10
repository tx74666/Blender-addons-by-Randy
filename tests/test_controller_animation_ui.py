"""Controller UI dispatch and stale/Source refusal without native execution."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class Action(dict):
    def __init__(self, name):
        super().__init__()
        self.name, self.library = name, None

    def __bool__(self):
        return True

    @property
    def frame_range(self):
        raise AssertionError('Drawing read Action frame_range')


class Layout:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.enabled = True

    def row(self, **_options):
        return Layout(self.events)

    def column(self, **_options):
        return Layout(self.events)

    def prop(self, owner, name, **options):
        self.events.append(('prop', owner, name, options))

    def label(self, **options):
        self.events.append(('label', options))

    def operator(self, identifier, **options):
        result = NS()
        self.events.append(('operator', identifier, options, result, self.enabled))
        return result


class ControllerUI(unittest.TestCase):
    def setUp(self):
        self.package_name = '_controller_ui_' + uuid.uuid4().hex
        package = ModuleType(self.package_name)
        package.__path__ = [str(SOURCE)]
        props = ModuleType('bpy.props')
        props.StringProperty = props.IntProperty = props.BoolProperty = props.FloatProperty = lambda **_options: None
        types = ModuleType('bpy.types')
        types.Operator = type('Operator', (), {})
        animation = ModuleType(self.package_name + '.animation')
        animation._job = None
        animation._link_idle = Mock(return_value=True)
        worklist = ModuleType(self.package_name + '.animation_worklist')
        self.backend = ModuleType(self.package_name + '.controller_animation')
        self.backend.status = Mock(return_value=dict(state='CANDIDATE', can_convert=True,
            frame_start=1., frame_end=60., has_record=False, message=''))
        self.backend.convert = Mock()
        patched = patch.dict(sys.modules, {self.package_name: package,
            'bpy.props': props, 'bpy.types': types, animation.__name__: animation,
            worklist.__name__: worklist,
            self.backend.__name__: self.backend})
        patched.start()
        self.addCleanup(patched.stop)
        setattr(package, 'controller_animation', self.backend)
        spec = importlib.util.spec_from_file_location(self.package_name + '.controller_animation_ui',
                                                     SOURCE / 'controller_animation_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ui)
        self.action = Action('Current Custom')
        self.rig = NS(name='Main Rig', type='ARMATURE', library=None,
                      animation_data=NS(action=self.action, action_slot_handle=7))
        self.item = NS(item_id='current-row', side='CUSTOM', rig=self.rig,
                       custom_action=self.action, custom_slot=7)
        self.saved = NS(active_index=0, items=[self.item], controller_auto_reduce=True,
            controller_show_advanced=False, controller_position_tolerance_mm=1.,
            controller_rotation_tolerance_degrees=.25)
        self.context = NS(mode='POSE', area=NS(tag_redraw=Mock()),
            scene=NS(character_designer_animation_worklist=self.saved, objects={self.rig.name:self.rig},
                     tool_settings=NS(use_keyframe_insert_auto=False)),
            view_layer=NS(objects={self.rig.name:self.rig}))
        self.target = NS(id_data=self.rig, bone={'character_designer_limb_ik_role':'HAND_IK'})
        self.context.object = self.rig
        self.context.selected_pose_bones = [self.target]

    def operator(self):
        operator = self.ui.CHARACTERDESIGNER_OT_make_controller_animation()
        operator.item_id, operator.rig_name = self.item.item_id, self.rig.name
        operator.action_name, operator.slot_handle = self.action.name, 7
        operator.report = Mock()
        return operator

    def move_operator(self):
        operator = self.ui.CHARACTERDESIGNER_OT_apply_controller_move_across_clip()
        operator.item_id, operator.rig_name = self.item.item_id, self.rig.name
        operator.action_name, operator.slot_handle = self.action.name, 7
        operator.report = Mock()
        return operator

    def test_execution_converts_once_full_clip_without_extra_controls(self):
        self.assertEqual(self.operator().execute(self.context), {'FINISHED'})
        self.backend.convert.assert_called_once_with(self.context, rig=self.rig, activate=True,
            auto_reduce=True, position_tolerance_meters=.001,
            rotation_tolerance_radians=.004363323129985824)

    def test_Source_refuses_without_backend_import_or_conversion(self):
        self.item.side = 'SOURCE'
        with patch.object(self.ui, '_backend', side_effect=AssertionError('Source imported backend')):
            self.assertEqual(self.operator().execute(self.context), {'CANCELLED'})
        self.backend.convert.assert_not_called()

    def test_stale_row_Action_or_slot_refuses_before_backend(self):
        for field, value in [('item_id','different-row'),('action_name','different-action'),('slot_handle',8)]:
            operator = self.operator()
            setattr(operator, field, value)
            with patch.object(self.ui, '_backend', side_effect=AssertionError('stale request imported backend')):
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.backend.convert.assert_not_called()

    def test_service_refusal_and_failure_return_cancelled(self):
        self.backend.status.return_value = dict(state='UNAVAILABLE', can_convert=False, message='Finish the pose test.')
        self.assertEqual(self.operator().execute(self.context), {'CANCELLED'})
        self.backend.convert.assert_not_called()
        self.backend.status.return_value = dict(state='CANDIDATE', can_convert=True)
        self.backend.convert.side_effect = ValueError('Native sample verification refused.')
        self.assertEqual(self.operator().execute(self.context), {'CANCELLED'})

    def test_draw_does_not_import_or_prepare_service(self):
        sys.modules.pop(self.backend.__name__)
        layout = Layout()
        with patch.object(self.ui, '_backend', side_effect=AssertionError('draw imported backend')):
            self.ui.draw_controller_animation(layout, self.context)
        self.assertEqual([event[2]['text'] for event in layout.events if event[0]=='operator'],
                         ['Generate Controller Custom'])
        self.backend.status.assert_not_called()
        self.backend.convert.assert_not_called()

    def test_recorded_controller_has_real_rebuild_dispatch_and_truthful_label(self):
        self.backend.status.return_value = dict(state='CONTROLLER',can_convert=True,
            frame_start=1.,frame_end=60.,has_record=True,message='')
        layout = Layout()
        self.ui.draw_controller_animation(layout, self.context)
        self.assertIn('Controller Animation',[event[1]['text'] for event in layout.events if event[0]=='label'])
        self.assertEqual([event[2]['text'] for event in layout.events if event[0]=='operator'],
                         ['Generate New Controller Custom','Apply Pending Move to Whole Clip'])
        self.assertEqual(self.operator().execute(self.context),{'FINISHED'})
        self.backend.convert.assert_called_once()

    def test_move_dispatches_only_explicit_current_targets_to_retained_service(self):
        service=NS(move_across_clip=Mock())
        with patch.object(self.ui,'_move_backend',return_value=service):
            self.assertEqual(self.move_operator().execute(self.context),{'FINISHED'})
        service.move_across_clip.assert_called_once_with(self.context,rig=self.rig,targets=(self.target,))

    def test_move_Source_stale_or_non_target_refuses_before_service_import(self):
        with patch.object(self.ui,'_move_backend',side_effect=AssertionError('refusal imported service')):
            self.item.side='SOURCE'
            self.assertEqual(self.move_operator().execute(self.context),{'CANCELLED'})
            self.item.side='CUSTOM'
            op=self.move_operator();op.slot_handle=8
            self.assertEqual(op.execute(self.context),{'CANCELLED'})
            self.target.bone=None
            self.assertEqual(self.move_operator().execute(self.context),{'CANCELLED'})

    def test_move_draw_reads_only_roles_and_current_metadata(self):
        self.backend.status.return_value = dict(state='CONTROLLER',can_convert=True,
            frame_start=1.,frame_end=60.,has_record=True,message='')
        self.action[self.ui.RECORD]='marker hint'
        with patch.object(self.ui,'_move_backend',side_effect=AssertionError('draw imported move')):
            layout=Layout()
            self.ui.draw_controller_animation(layout,self.context)
            event=next(e for e in layout.events if e[0]=='operator' and e[1].endswith('apply_controller_move_across_clip'))
            self.assertTrue(event[4])
            self.assertTrue(self.ui.CHARACTERDESIGNER_OT_apply_controller_move_across_clip.poll(self.context))
            self.target.bone=None
            layout=Layout()
            self.ui.draw_controller_animation(layout,self.context)
            event=next(e for e in layout.events if e[0]=='operator' and e[1].endswith('apply_controller_move_across_clip'))
            self.assertFalse(event[4])
            self.assertFalse(self.ui.CHARACTERDESIGNER_OT_apply_controller_move_across_clip.poll(self.context))

    def test_move_service_refusal_is_cancelled_and_reported(self):
        service=NS(move_across_clip=Mock(side_effect=ValueError('Move the selected target first.')))
        op=self.move_operator()
        with patch.object(self.ui,'_move_backend',return_value=service):
            self.assertEqual(op.execute(self.context),{'CANCELLED'})
        op.report.assert_called_once_with({'ERROR'},'Move the selected target first.')

    def test_Source_draw_requests_Custom_without_switching(self):
        self.item.side='SOURCE'
        layout=Layout()
        self.ui.draw_controller_animation(layout,self.context)
        self.assertIn('Select Custom to convert.',[event[1]['text'] for event in layout.events if event[0]=='label'])
        self.assertFalse(next(event for event in layout.events if event[0]=='operator')[4])
        self.backend.status.assert_not_called()
        self.backend.convert.assert_not_called()
        self.assertEqual(self.item.side,'SOURCE')

    def test_partial_or_old_row_metadata_draws_disabled_without_import(self):
        for field in ('rig','custom_action','custom_slot'):
            value=getattr(self.item,field)
            delattr(self.item,field)
            try:
                layout=Layout()
                with patch.object(self.ui,'_backend',side_effect=AssertionError('draw imported backend')):
                    self.ui.draw_controller_animation(layout,self.context)
                self.assertFalse(next(event for event in layout.events if event[0]=='operator')[4])
                self.assertFalse(self.ui.CHARACTERDESIGNER_OT_make_controller_animation.poll(self.context))
            finally:
                setattr(self.item,field,value)
        self.backend.convert.assert_not_called()


if __name__=='__main__':
    unittest.main()
