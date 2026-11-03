"""Registration/draw wiring and owned import-copy dispatch, without Blender."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import test_animation_worklist_ui as ui_fixture
import test_animation_worklist as worklist_fixture


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class ControllerWorklistUI(ui_fixture.WorklistUI):
    def test_controller_class_is_in_existing_registration_chain_exactly_once(self):
        controller = sys.modules[self.ui.__package__ + '.controller_animation_ui']
        cls = controller.CHARACTERDESIGNER_OT_make_controller_animation
        self.assertEqual(sum(value is cls for value in self.ui.WORKLIST_CLASSES), 1)
        identifiers = [getattr(value, 'bl_idname', value.__name__) for value in self.ui.WORKLIST_CLASSES]
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_controller_draw_follows_list_and_precedes_existing_edit_tools(self):
        calls = []
        with patch.object(self.ui, '_draw_worklist_actions', side_effect=lambda *_:calls.append('list')), \
                patch.object(self.ui, 'draw_controller_animation', side_effect=lambda *_:calls.append('controller')), \
                patch.object(self.ui, '_draw_key_reduce', side_effect=lambda *_:calls.append('keys')), \
                patch.object(self.ui, '_draw_rigging', side_effect=lambda *_:calls.append('rigging')):
            self.ui.draw_worklist(ui_fixture.Layout(), self.context)
        self.assertEqual(calls, ['list', 'controller', 'keys', 'rigging'])

    def test_controller_draw_not_offered_for_empty_list(self):
        self.state.items = []
        with patch.object(self.ui, 'draw_controller_animation') as draw:
            self.ui.draw_worklist(ui_fixture.Layout(), self.context)
        draw.assert_not_called()

    def test_controller_missing_rig_or_active_Action_keeps_whole_worklist_drawable(self):
        item=self.state.items[0]
        rig=item.rig
        for missing_rig in (True,False):
            item.rig=None if missing_rig else rig
            rig.animation_data.action=None
            self.ui.draw_worklist(ui_fixture.Layout(),self.context)
            self.assertFalse(self.ui._active_side(item,'CUSTOM'))
        item.rig=rig


class ControllerImport(worklist_fixture.WorklistTests):
    def setup_controller(self):
        backend = ModuleType(self.worklist.__package__ + '.controller_animation')
        backend.build_controller_channels = Mock(return_value=dict(conversion_state='CONTROLLER', channels={}))
        backend.apply_to_action = Mock(return_value=dict(conversion_state='CONTROLLER', metadata={'whole_clip':True}))
        patcher = patch.dict(sys.modules, {backend.__name__:backend})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source_action = worklist_fixture.ID(name='Read-only Source',library=NS(filepath='source.blend'))
        self.custom_action = worklist_fixture.ID(name='New unbound Custom',library=None)
        self.worklist._slot = Mock(return_value=NS(handle=7))
        return backend

    def test_controller_import_uses_source_reader_and_independent_destination_writer(self):
        backend = self.setup_controller()
        rig = object()
        result = self.worklist._populate_controller_custom(self.context,rig,self.source_action,self.custom_action)
        self.assertEqual(result,'CONTROLLER')
        backend.build_controller_channels.assert_called_once_with(self.context,rig,self.source_action,7)
        backend.apply_to_action.assert_called_once_with(self.custom_action,7,backend.build_controller_channels.return_value)

    def test_controller_import_refuses_input_Action_as_writer_target(self):
        backend = self.setup_controller()
        with self.assertRaisesRegex(ValueError,'own local Custom copy'):
            self.worklist._populate_controller_custom(self.context,object(),self.source_action,self.source_action)
        backend.build_controller_channels.assert_not_called()
        backend.apply_to_action.assert_not_called()

    def test_controller_import_native_FK_result_does_not_claim_IK(self):
        backend = self.setup_controller()
        backend.build_controller_channels.return_value=dict(conversion_state='NATIVE_FK',channels={},metadata=None)
        backend.apply_to_action.return_value=dict(conversion_state='NATIVE_FK',metadata=None)
        self.assertEqual(self.worklist._populate_controller_custom(self.context,object(),self.source_action,self.custom_action),'NATIVE_FK')
        backend.apply_to_action.return_value=dict(conversion_state='NATIVE_FK',metadata={'fake_IK':True})
        with self.assertRaisesRegex(ValueError,'cannot claim'):
            self.worklist._populate_controller_custom(self.context,object(),self.source_action,self.custom_action)

    def test_controller_import_unknown_or_writer_failure_prevents_success(self):
        backend = self.setup_controller()
        backend.build_controller_channels.return_value=dict(conversion_state='UNKNOWN')
        with self.assertRaisesRegex(ValueError,'did not establish'):
            self.worklist._populate_controller_custom(self.context,object(),self.source_action,self.custom_action)
        backend.apply_to_action.assert_not_called()
        backend.build_controller_channels.return_value=dict(conversion_state='CONTROLLER')
        backend.apply_to_action.side_effect=ValueError('owned copy writing failed')
        with self.assertRaisesRegex(ValueError,'owned copy writing failed'):
            self.worklist._populate_controller_custom(self.context,object(),self.source_action,self.custom_action)


def load_tests(loader, _suite, _pattern):
    suite=unittest.TestSuite()
    for cls in (ControllerWorklistUI,ControllerImport):
        suite.addTests(cls(name) for name in loader.getTestCaseNames(cls) if name.startswith('test_controller_'))
    return suite


if __name__=='__main__':
    unittest.main()
