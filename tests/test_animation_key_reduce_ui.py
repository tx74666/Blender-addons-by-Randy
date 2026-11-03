"""Key-reduction UI contracts without Blender, sampling or native writes."""

from types import ModuleType
import sys
import unittest
from unittest.mock import Mock, patch

import test_animation_worklist_ui as fixtures


class KeyReduceUI(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorklistUI('runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.ui, self.context = self.fixture.ui, self.fixture.context
        self.item = self.fixture.state.items[0]
        self.item.custom_slot = 41
        self.item.rig.animation_data.action_slot_handle = 41
        self.context.mode = 'POSE'
        self.context.window_manager.invoke_props_dialog = Mock(return_value={'RUNNING_MODAL'})
        self.backend = ModuleType(self.ui.__package__ + '.animation_key_reduce')
        self.backend.prepare = Mock(return_value=object())
        self.backend.apply, self.backend.compare = Mock(), Mock()
        self.backend.status = Mock(return_value=dict(before_keys=120, after_keys=36, active_before=False))

    def load_backend(self):
        package = sys.modules[self.ui.__package__]
        self.enterContext(patch.dict(sys.modules, {self.backend.__name__: self.backend}))
        self.enterContext(patch.object(package, 'animation_key_reduce', self.backend, create=True))

    def operator(self):
        value = self.ui.CHARACTERDESIGNER_OT_worklist_simplify_keys()
        value.position_error, value.rotation_error, value.scale_error = .0008, .25, .002
        return value

    def draw(self):
        layout = fixtures.Layout()
        self.ui._draw_key_reduce(layout, self.context, self.item)
        return layout.records

    def test_first_draw_and_poll_do_not_import_backend_or_prepare_motion(self):
        self.assertNotIn(self.backend.__name__, sys.modules)
        with patch.object(self.ui, '_key_reduce', side_effect=AssertionError('no import during drawing')):
            records = self.draw()
            self.assertTrue(self.ui.CHARACTERDESIGNER_OT_worklist_simplify_keys.poll(self.context))
            self.assertFalse(self.ui.CHARACTERDESIGNER_OT_worklist_compare_keys.poll(self.context))
        buttons = [value[0] for kind, value, _enabled in records if kind == 'operator']
        self.assertEqual(buttons, ['character_designer.worklist_simplify_keys'])
        self.backend.prepare.assert_not_called()
        self.assertNotIn(self.backend.__name__, sys.modules)

    def test_dialog_defers_prepare_until_chosen_tolerances_are_executed(self):
        self.load_backend()
        operator = self.operator()
        self.assertEqual(operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.context.window_manager.invoke_props_dialog.assert_called_once_with(operator, width=360)
        self.backend.prepare.assert_not_called()
        self.backend.apply.assert_not_called()
        operator.layout = fixtures.Layout()
        operator.draw(self.context)
        props = [value[0] for kind, value, _enabled in operator.layout.records if kind == 'prop']
        self.assertEqual(props, ['position_error', 'rotation_error', 'scale_error'])
        definitions = type(operator).__annotations__
        self.assertEqual([definitions[name]['default'] for name in props], [.0005, .1, .001])
        self.assertEqual(definitions['position_error']['unit'], 'LENGTH')
        self.assertNotEqual(definitions['rotation_error'].get('unit'), 'ROTATION')

    def test_execute_passes_degrees_and_fraction_then_applies_the_exact_plan(self):
        self.load_backend()
        events = []
        plan = object()
        self.backend.prepare.side_effect = lambda *args, **kwargs: events.append('prepare') or plan
        self.backend.apply.side_effect = lambda *args: events.append('apply')
        operator = self.operator()
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.assertEqual(events, ['prepare', 'apply'])
        self.backend.prepare.assert_called_once_with(
            self.context, position_error=.0008, rotation_error=.25, scale_error=.002)
        self.backend.apply.assert_called_once_with(self.context, plan)
        self.assertEqual(self.fixture.state.status, 'Keys: 120 → 36')
        self.assertFalse(self.fixture.state.has_error)

    def test_prepare_refusal_is_visible_and_never_calls_apply(self):
        self.load_backend()
        self.backend.prepare.side_effect = ValueError('Finish the current pose session.')
        operator = self.operator()
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.backend.apply.assert_not_called()
        self.assertTrue(self.fixture.state.has_error)
        self.assertEqual(operator.last_report, ({'ERROR'}, 'Finish the current pose session.'))

    def test_apply_refusal_does_not_show_success_counts(self):
        self.load_backend()
        self.backend.apply.side_effect = RuntimeError('The Custom changed after preparation.')
        self.assertEqual(self.operator().execute(self.context), {'CANCELLED'})
        self.backend.status.assert_not_called()
        self.assertTrue(self.fixture.state.has_error)
        self.assertIn('changed after preparation', self.fixture.state.status)

    def test_feedback_expiry_after_success_is_not_reported_as_apply_failure(self):
        self.load_backend()
        self.backend.status.side_effect = ReferenceError('Pair is no longer displayable.')
        self.assertEqual(self.operator().execute(self.context), {'FINISHED'})
        self.backend.apply.assert_called_once()
        self.assertFalse(self.fixture.state.has_error)
        self.assertEqual(self.fixture.state.status, 'Created a simplified Custom version.')

    def test_simplify_poll_requires_direct_current_custom_slot_and_idle_pose_or_object(self):
        self.load_backend()
        poll = self.ui.CHARACTERDESIGNER_OT_worklist_simplify_keys.poll
        self.assertTrue(poll(self.context))
        for owner, field, value in ((self.item, 'side', 'SOURCE'),
                                     (self.context, 'mode', 'EDIT_ARMATURE'),
                                     (self.item.rig.animation_data, 'action', object()),
                                     (self.item.rig.animation_data, 'action_slot_handle', 77)):
            with self.subTest(field=field), patch.object(owner, field, value):
                self.assertFalse(poll(self.context))
        with patch.object(self.ui, '_idle', return_value=False):
            self.assertFalse(poll(self.context))
        self.backend.status.return_value['active_before'] = True
        self.assertFalse(poll(self.context))
        self.backend.prepare.assert_not_called()

    def test_compare_stays_available_while_before_action_is_viewed(self):
        self.load_backend()
        self.backend.status.return_value['active_before'] = True
        self.item.rig.animation_data.action = object()
        self.assertTrue(self.ui.CHARACTERDESIGNER_OT_worklist_compare_keys.poll(self.context))
        self.assertFalse(self.ui.CHARACTERDESIGNER_OT_worklist_simplify_keys.poll(self.context))
        for original in (True, False):
            operator = self.ui.CHARACTERDESIGNER_OT_worklist_compare_keys()
            operator.original = original
            self.assertEqual(operator.execute(self.context), {'FINISHED'})
            self.backend.compare.assert_called_with(self.context, original=original)
        self.backend.prepare.assert_not_called()
        self.backend.apply.assert_not_called()

    def test_compare_poll_and_errors_keep_backend_authority(self):
        self.load_backend()
        poll = self.ui.CHARACTERDESIGNER_OT_worklist_compare_keys.poll
        for owner, field, value in ((self.item, 'side', 'SOURCE'), (self.context, 'mode', 'EDIT_MESH')):
            with self.subTest(field=field), patch.object(owner, field, value):
                self.assertFalse(poll(self.context))
        with patch.object(self.ui, '_idle', return_value=False):
            self.assertFalse(poll(self.context))
        self.backend.status.return_value = None
        self.assertFalse(poll(self.context))
        self.backend.compare.side_effect = ValueError('Select the same retained pair.')
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_compare_keys()
        operator.original = True
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(operator.last_report, ({'ERROR'}, 'Select the same retained pair.'))

    def test_draw_only_reads_counts_and_routes_the_same_pair_buttons(self):
        self.load_backend()
        for before in (False, True):
            with self.subTest(before=before):
                self.backend.status.return_value['active_before'] = before
                records = self.draw()
                labels = [value['text'] for kind, value, _enabled in records if kind == 'label']
                self.assertEqual(labels, ['Keys: 120 → 36'])
                buttons = {value[1]['text']: (value, enabled)
                           for kind, value, enabled in records if kind == 'operator'}
                self.assertEqual(set(buttons), {'Simplify Keys', 'Before', 'After'})
                self.assertEqual(buttons['Simplify Keys'][1], not before)
                for label, original in [('Before', True), ('After', False)]:
                    value, enabled = buttons[label]
                    self.assertTrue(enabled)
                    self.assertIs(value[2].original, original)
                    self.assertEqual(value[1]['depress'], before == original)
        self.backend.prepare.assert_not_called()
        self.backend.apply.assert_not_called()
        self.backend.compare.assert_not_called()

    def test_source_has_no_custom_tools_and_stale_pair_has_no_comparison(self):
        self.load_backend()
        self.item.side = 'SOURCE'
        self.assertEqual(self.draw(), [])
        self.backend.status.assert_not_called()
        self.item.side = 'CUSTOM'
        self.backend.status.side_effect = ValueError('Pair metadata is stale.')
        buttons = [value[0] for kind, value, _enabled in self.draw() if kind == 'operator']
        self.assertEqual(buttons, ['character_designer.worklist_simplify_keys'])

    def test_full_worklist_retains_two_columns_and_registers_standard_undo_operators(self):
        layout = fixtures.Layout()
        with self.fixture.base_pose_modules() as (pose, _state), \
                patch.object(pose, 'natural_active', Mock(return_value=False), create=True):
            self.ui.draw_worklist(layout, self.context)
        headers = [value['text'] for kind, value, _enabled in layout.records
                   if kind == 'label' and value['text'] in {'Source', 'Custom'}]
        self.assertEqual(headers, ['Source', 'Custom'])
        positions = [(kind, value[0] if kind == 'operator' else value[0][0])
                     for kind, value, _enabled in layout.records if kind in {'list', 'operator'}]
        self.assertLess(positions.index(('list', 'CHARACTERDESIGNER_UL_animation_worklist')),
                        positions.index(('operator', 'character_designer.worklist_simplify_keys')))
        for cls in (self.ui.CHARACTERDESIGNER_OT_worklist_simplify_keys,
                    self.ui.CHARACTERDESIGNER_OT_worklist_compare_keys):
            self.assertIn(cls, self.ui.WORKLIST_CLASSES)
            self.assertIn('UNDO', cls.bl_options)


if __name__ == '__main__':
    unittest.main()
