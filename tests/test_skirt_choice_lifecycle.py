"""Real choice-service integration with public Rig routing and remove cleanup."""
import ast
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import test_animation_physics as fixture
import test_skirt_wiggle_ui as rig_ui_fixture


ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'
physics = fixture.physics


def cleanup_function():
    tree = ast.parse((ROOT/'skirt_rig.py').read_text(encoding='utf-8'))
    names = {'RECORD_KEY', 'RIG_KEY', 'OWNER_KEY', 'PARENT_KEY', 'ATTACHMENT_BACKUP_KEY', 'ATTACHMENT_PARENT_KEY'}
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == '_clear_source_properties'
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names
                                                    for target in node.targets)]
    package = ModuleType('_choice_cleanup_fixture')
    package.__path__ = []
    for name, values in {
        'animation_physics': {'SKIRT_CHOICE': physics.SKIRT_CHOICE},
        'skirt_wiggle': {'STATE_KEY': 'character_designer_skirt_wiggle_v1',
                         'STATE_SCENE_KEY': 'character_designer_skirt_wiggle_scene_v1'},
        'skirt_original_mode': {'CORRECTIONS': 'test_pose_corrections'},
        'skirt_motion_profiles': {'PROFILE_KEY': 'character_designer_dress_motion_v1'},
    }.items():
        module = ModuleType(package.__name__+'.'+name)
        module.__dict__.update(values)
        setattr(package, name, module)
    scope = {'__package__': package.__name__}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT/'skirt_rig.py'), 'exec'), scope)
    def run(source):
        with patch.dict(sys.modules, {package.__name__: package}):
            scope['_clear_source_properties'](source)
    return run


class ChoiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PhysicsTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.record['physics'] = None
        self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
        self.actual = 'MANUAL'
        self.f.wiggle.mode = lambda source: self.actual
        self.f.wiggle.set_mode = self.apply_mode
        self.calls = []

    def apply_mode(self, context, source, mode):
        self.calls.append(mode)
        self.actual = mode

    def select(self, mode):
        return physics.set_skirt_mode(self.f.context, self.f.source.name, mode,
                                      expected_owner=self.f.record['owner'])

    def test_remove_cleanup_then_recreate_has_new_owner_and_unset_automatic_default(self):
        self.select('MANUAL')
        old_choice = json.loads(self.f.source[physics.SKIRT_CHOICE])
        self.f.source['author_notes'] = 'retained latest artist metadata'
        self.f.source['character_designer_dress_motion_v1'] = 'old backend profile'
        cleanup_function()(self.f.source)
        self.assertNotIn(physics.SKIRT_CHOICE, self.f.source)
        self.assertNotIn(physics.SKIRT_RECORD, self.f.source)
        self.assertNotIn('character_designer_dress_motion_v1', self.f.source)
        self.assertEqual(self.f.source['author_notes'], 'retained latest artist metadata')
        self.f.record = dict(self.f.record, owner='new setup owner')
        self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
        self.f.source[physics.SKIRT_RIG] = self.f.rig
        self.f.rig['character_designer_skirt_owner'] = 'new setup owner'
        self.f.rig.data['character_designer_skirt_owner'] = 'new setup owner'
        choice = physics._skirt_choice(self.f.source, self.f.record)
        self.assertNotEqual(choice['owner'], old_choice['owner'])
        self.assertEqual((choice['owner'], choice['mode']), ('new setup owner', 'AUTOMATIC'))
        self.select('MANUAL')
        self.assertEqual(json.loads(self.f.source[physics.SKIRT_CHOICE])['owner'], 'new setup owner')

    def test_cleanup_is_idempotent_without_choice_or_unrelated_data_loss(self):
        self.f.source['artist_data'] = {'latest': 42}
        clear = cleanup_function()
        clear(self.f.source)
        clear(self.f.source)
        self.assertEqual(dict(self.f.source), {'artist_data': {'latest': 42}})

    def test_latest_rig_manual_choice_updates_desired_and_survives_controls_reconcile(self):
        self.select('AUTOMATIC')
        self.calls.clear()
        ui = rig_ui_fixture.ui
        operator = ui.CHARACTERDESIGNER_OT_dress_wiggle_mode()
        operator.mode = 'MANUAL'
        workflow = SimpleNamespace(_source=lambda context: self.f.source, _report=lambda *args, **kwargs: None)
        with patch.object(ui, '_workflow', return_value=workflow), \
             patch.object(ui, '_physics_choices', return_value=physics), \
             patch.object(ui.skirt_rig, 'read_record', return_value=self.f.record, create=True), \
             patch.object(ui, '_show_layer'):
            self.assertEqual(operator.execute(self.f.context), {'FINISHED'})
        choice = physics._skirt_choice(self.f.source, self.f.record)
        self.assertEqual((self.actual, choice['mode'], choice['pending']), ('MANUAL', 'MANUAL', False))
        self.assertEqual(self.calls, ['MANUAL'])
        physics.apply_pending_skirt_modes(self.f.context, self.f.rig, reconcile=True)
        self.assertEqual(self.calls, ['MANUAL'])
        self.assertEqual(self.actual, 'MANUAL')

    def test_rig_mode_owner_refusal_retains_previous_desired_and_actual_mode(self):
        self.select('AUTOMATIC')
        old_choice = self.f.source[physics.SKIRT_CHOICE]
        self.calls.clear()
        ui = rig_ui_fixture.ui
        operator = ui.CHARACTERDESIGNER_OT_dress_wiggle_mode()
        operator.mode = 'MANUAL'
        workflow = SimpleNamespace(_source=lambda context: self.f.source, _report=lambda *args, **kwargs: None)
        with patch.object(ui, '_workflow', return_value=workflow), \
             patch.object(ui, '_physics_choices', return_value=physics), \
             patch.object(ui.skirt_rig, 'read_record', return_value={'owner': 'foreign setup'}, create=True), \
             patch.object(ui, '_show_layer') as display:
            self.assertEqual(operator.execute(self.f.context), {'CANCELLED'})
        display.assert_not_called()
        self.assertEqual(self.f.source[physics.SKIRT_CHOICE], old_choice)
        self.assertEqual((self.actual, self.calls), ('AUTOMATIC', []))


if __name__ == '__main__':
    unittest.main()
