"""Real option transport and read-only current-frame presentation contracts."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import test_controller_animation_ui as fixture


class ControllerPanelTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.ControllerUI('test_execution_converts_once_full_clip_without_extra_controls')
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.ui = self.case.ui

    def test_stricter_options_and_no_reduction_reach_the_real_service_call(self):
        operator = self.case.operator()
        self.case.saved.controller_auto_reduce = False
        self.case.saved.controller_position_tolerance_mm = .2
        self.case.saved.controller_rotation_tolerance_degrees = .1
        self.assertEqual(operator.execute(self.case.context), {'FINISHED'})
        self.case.backend.convert.assert_called_once_with(self.case.context, rig=self.case.rig,
            activate=True, auto_reduce=False, position_tolerance_meters=.0002,
            rotation_tolerance_radians=.1*.017453292519943295)

    def test_invalid_or_looser_tolerance_never_dispatches_conversion(self):
        for field, value in (('controller_auto_reduce', 1), ('controller_position_tolerance_mm', 1.001),
                             ('controller_rotation_tolerance_degrees', .251), ('controller_position_tolerance_mm', 0.),
                             ('controller_rotation_tolerance_degrees', float('nan'))):
            operator = self.case.operator(); previous = getattr(self.case.saved, field)
            setattr(self.case.saved, field, value)
            with self.subTest(field=field, value=value):
                self.assertEqual(operator.execute(self.case.context), {'CANCELLED'})
                self.case.backend.convert.assert_not_called()
            setattr(self.case.saved, field, previous)

    def test_panel_choices_feed_execute_and_keep_advanced_fields(self):
        saved = self.case.saved
        saved.controller_auto_reduce = False
        saved.controller_show_advanced = True
        saved.controller_position_tolerance_mm = .5
        saved.controller_rotation_tolerance_degrees = .125
        layout = fixture.Layout(); self.ui.draw_controller_animation(layout, self.case.context)
        names = {event[2] for event in layout.events if event[0] == 'prop'}
        self.assertTrue({'controller_auto_reduce', 'controller_show_advanced',
            'controller_position_tolerance_mm', 'controller_rotation_tolerance_degrees'} <= names)
        self.case.backend.convert.assert_not_called()
        self.assertEqual(self.case.operator().execute(self.case.context), {'FINISHED'})
        self.case.backend.convert.assert_called_once_with(self.case.context, rig=self.case.rig,
            activate=True, auto_reduce=False, position_tolerance_meters=.0005,
            rotation_tolerance_radians=.125*.017453292519943295)

    def test_ratio_is_a_result_from_bounded_metadata_not_a_target_property(self):
        self.case.backend.status.return_value = dict(state='CONTROLLER', can_convert=True, has_record=True)
        self.case.action[self.ui.RECORD] = json.dumps(dict(schema='character-designer.controller-animation/1',
            diagnostics=dict(before_points=1000, after_points=400)))
        layout = fixture.Layout(); self.ui.draw_controller_animation(layout, self.case.context)
        labels = [event[1]['text'] for event in layout.events if event[0] == 'label']
        self.assertIn('Controller keys: 400 / 1,000 (60.0% fewer)', labels)
        self.assertFalse(any('ratio' in event[2] for event in layout.events if event[0] == 'prop'))
        for before, after in ((0, 0), (10, 11), (True, 1)):
            self.case.action[self.ui.RECORD] = json.dumps(dict(schema='character-designer.controller-animation/1',
                diagnostics=dict(before_points=before, after_points=after)))
            self.assertIsNone(self.ui._reduction_summary(self.case.action))

    def test_Auto_Key_only_applies_to_current_frame_not_whole_clip_dispatch(self):
        self.case.backend.status.return_value = dict(state='CONTROLLER', can_convert=True, has_record=True)
        self.case.action[self.ui.RECORD] = 'metadata hint'
        self.case.context.scene.tool_settings.use_keyframe_insert_auto = True
        layout = fixture.Layout(); self.ui.draw_controller_animation(layout, self.case.context)
        move = next(event for event in layout.events if event[0] == 'operator'
                    and event[1].endswith('apply_controller_move_across_clip'))
        self.assertFalse(move[4])
        self.assertFalse(self.ui.CHARACTERDESIGNER_OT_apply_controller_move_across_clip.poll(self.case.context))
        with patch.object(self.ui, '_move_backend', side_effect=AssertionError('Auto Key imported Move')):
            self.assertEqual(self.case.move_operator().execute(self.case.context), {'CANCELLED'})
        self.assertTrue(any(event[0] == 'prop' and event[2] == 'use_keyframe_insert_auto' for event in layout.events))

    def test_corrupt_panel_values_disable_the_button_without_draw_failure(self):
        self.case.saved.controller_position_tolerance_mm = float('nan')
        layout = fixture.Layout(); self.ui.draw_controller_animation(layout, self.case.context)
        self.assertFalse(next(event for event in layout.events if event[0] == 'operator')[4])
        self.case.backend.convert.assert_not_called()

    def test_transient_native_fields_have_closed_defaults_and_upper_bounds(self):
        tree = ast.parse((fixture.SOURCE/'animation_worklist_ui.py').read_text(encoding='utf8'))
        state = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                     and node.name == 'CharacterDesignerAnimationWorklistState')
        # Blender properties are annotations, not assigned Python values.
        fields = {node.target.id: node.annotation for node in state.body if isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name) and node.target.id.startswith('controller_')}
        self.assertEqual(set(fields), {'controller_auto_reduce', 'controller_show_advanced',
            'controller_position_tolerance_mm', 'controller_rotation_tolerance_degrees'})
        values = {name: {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
                  for name, call in fields.items()}
        self.assertIs(values['controller_auto_reduce']['default'], True)
        self.assertEqual(values['controller_position_tolerance_mm']['max'], 1.)
        self.assertEqual(values['controller_rotation_tolerance_degrees']['max'], .25)
        self.assertTrue(all(row['options'] == {'SKIP_SAVE'} for row in values.values()))


class CurrentFrameModeTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.ControllerUI('test_execution_converts_once_full_clip_without_extra_controls')
        self.addCleanup(self.case.doCleanups); self.case.setUp()
        self.ui = self.case.ui
        self.rig = self.case.rig
        self.values = {('ARM', 'L'): 1., ('ARM', 'R'): 1., ('LEG', 'L'): 0., ('LEG', 'R'): 0.}
        self.targets = {key: NS(name=key[0]+'.'+key[1]) for key in self.values}
        self.bones = {self.targets[key].name: {'ik_fk': value} for key, value in self.values.items()}
        self.inventory = {'rigs': {key: dict(target=target,
            chain=tuple(key[0]+role+'.'+key[1] for role in ('upper', 'lower', 'end')))
            for key, target in self.targets.items()}}
        self.context = self.case.context
        self.context.scene.frame_current = 14; self.context.scene.frame_subframe = 0.
        self.graph = NS(scene_eval=NS(frame_current=14, frame_subframe=0.))
        self.context.view_layer.depsgraph = self.graph
        self.context.view_layer.update = Mock(side_effect=AssertionError('draw updated dependencies'))
        self.context.evaluated_depsgraph_get = Mock(side_effect=AssertionError('draw requested evaluation'))
        self.rig.evaluated_get = Mock(return_value=NS(pose=NS(bones=self.bones),
                                                     is_evaluated=True, original=self.rig))
        self.context.selected_pose_bones = [NS(name=self.targets['ARM', 'L'].name, id_data=self.rig, parent=None)]

    def labels(self):
        layout = fixture.Layout()
        self.ui.draw_current_frame_modes(layout, self.context, self.rig, self.inventory)
        return [event[1]['text'] for event in layout.events if event[0] == 'label'], layout

    def test_selected_arm_status_does_not_misreport_different_leg_modes(self):
        labels, layout = self.labels()
        self.assertIn('Current frame · Left arm · IK', labels)
        self.assertIn('All arms / legs: Mixed', labels)
        self.assertFalse(any('Left leg:' in label for label in labels))
        buttons = [event for event in layout.events if event[0] == 'operator']
        self.assertEqual([event[2]['text'] for event in buttons], ['All IK', 'All FK'])
        self.assertTrue(all(event[2]['depress'] is False for event in buttons))

    def test_blend_has_an_explicit_weight_even_when_neither_button_is_pressed(self):
        self.bones[self.targets['ARM', 'L'].name]['ik_fk'] = .35
        labels, _layout = self.labels()
        self.assertIn('Current frame · Left arm · Blend 35.0% IK', labels)

    def test_returning_to_frame14_reads_current_evaluated_values_without_a_cache(self):
        for frame, value, text in ((14, 1., 'IK'), (15, 0., 'FK'), (14, 1., 'IK')):
            self.context.scene.frame_current = self.graph.scene_eval.frame_current = frame
            self.bones[self.targets['ARM', 'L'].name]['ik_fk'] = value
            labels, _layout = self.labels()
            self.assertIn('Current frame · Left arm · '+text, labels)
        self.context.view_layer.update.assert_not_called()
        self.context.evaluated_depsgraph_get.assert_not_called()

    def test_stale_frame_or_invalid_weight_never_guesses_from_original_RNA(self):
        self.context.scene.frame_current = 15
        labels, layout = self.labels()
        self.assertIn('Current-frame IK/FK state is updating.', labels)
        self.assertTrue(all(event[4] is False for event in layout.events if event[0] == 'operator'))
        self.context.scene.frame_current = 14
        self.bones[self.targets['ARM', 'L'].name]['ik_fk'] = float('nan')
        labels, _layout = self.labels()
        self.assertIn('The current IK/FK weight is outside its supported range.', labels)

    def test_native_finger_descendant_resolves_the_actual_arm_scope(self):
        end = self.inventory['rigs']['ARM', 'R']['chain'][-1]
        self.context.selected_pose_bones = [NS(name='Finger', id_data=self.rig, parent=NS(name=end, parent=None))]
        labels, _layout = self.labels()
        self.assertIn('Current frame · Right arm · IK', labels)

    def test_missing_evaluated_owner_does_not_fall_back_to_raw_object(self):
        self.rig.evaluated_get.return_value = self.rig
        labels, layout = self.labels()
        self.assertIn('Current-frame IK/FK state is not available yet.', labels)
        self.assertTrue(all(event[4] is False for event in layout.events if event[0] == 'operator'))

    def test_original_workspace_does_not_request_an_evaluated_graph(self):
        layout = fixture.Layout()
        self.ui.draw_current_frame_modes(layout, self.context, self.rig, None, original=True)
        self.assertIn('Current frame · Original bones · FK',
                      [event[1]['text'] for event in layout.events if event[0] == 'label'])
        self.rig.evaluated_get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
