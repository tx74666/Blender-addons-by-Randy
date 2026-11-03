"""Export-base metadata, fail-closed preflight and worker boundary contracts."""
import importlib.util
import ast
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


inputs = load('_test_export_inputs', 'animation_export_inputs.py')


class Action(dict):
    name = 'Custom'
    library = None
    is_action_layered = False


class ExportInputs(unittest.TestCase):
    def setUp(self):
        self.action = Action()
        self.rig = NS(name='Rig', animation_data=NS(action=self.action, use_tweak_mode=False))
        self.inventory = {'slot': 0, 'structure': 'approved-structure', '_fully_keyed': False,
                          'inputs': [{'path': 'location', 'index': 1, 'value': .2}], 'unkeyed_components': 1}

    def test_draw_status_never_scans_action_or_rig(self):
        with patch.object(inputs, '_inventory', side_effect=AssertionError('Drawing scanned the rig')):
            self.assertEqual(inputs.status(self.rig, self.action)['state'], 'CAPTURE_REQUIRED')
            self.action[inputs.RECORD] = 'saved'
            self.assertEqual(inputs.status(self.rig, self.action)['message'], 'Captured; checked when exporting.')

    def test_prepare_reuses_saved_values_without_adopting_later_artist_pose(self):
        with patch.object(inputs, '_inventory', side_effect=lambda *_args: json.loads(json.dumps(self.inventory))):
            saved = inputs.capture(self.rig, self.action)
            raw = self.action[inputs.RECORD]
            self.inventory['inputs'][0]['value'] = .9
            sealed = inputs.prepare(self.rig, self.action, frame_start=2.)
            self.assertEqual(sealed['inputs'], saved['inputs'])
            self.assertEqual(self.action[inputs.RECORD], raw)
            self.assertEqual(sealed['reference_frame'], 2.)

    def test_baseline_integrity_and_structure_changes_fail_closed(self):
        with patch.object(inputs, '_inventory', side_effect=lambda *_args: json.loads(json.dumps(self.inventory))):
            inputs.capture(self.rig, self.action)
            raw = self.action[inputs.RECORD]
            record = json.loads(raw)
            record['inputs'][0]['value'] = .9
            self.action[inputs.RECORD] = json.dumps(record)
            with self.assertRaisesRegex(ValueError, 'invalid'):
                inputs.prepare(self.rig, self.action, frame_start=1.)
            self.action[inputs.RECORD] = raw
            self.inventory['structure'] = 'changed-bone-inheritance'
            with self.assertRaisesRegex(ValueError, 'structure'):
                inputs.prepare(self.rig, self.action, frame_start=1.)

    def test_sparse_missing_baseline_refuses_without_writing_action(self):
        with patch.object(inputs, '_inventory', return_value=self.inventory.copy()):
            with self.assertRaisesRegex(ValueError, 'Capture Export Base Pose'):
                inputs.prepare(self.rig, self.action, frame_start=1.)
        self.assertFalse(self.action)

    def test_other_active_action_cannot_be_captured(self):
        self.rig.animation_data.action = Action()
        with self.assertRaisesRegex(ValueError, 'Activate'):
            inputs.capture(self.rig, self.action)
        self.assertFalse(self.action)

    def test_explicit_clear_authenticates_owner_and_changes_only_two_fields(self):
        package = ModuleType('_clear_owner_test')
        package.__path__ = []
        owner = ModuleType('_clear_owner_test.animation')
        owner._export_base_owner = lambda *_args: None
        self.action.update({inputs.RECORD: 'saved-base', inputs.COUNT: 2, 'artist_note': 'keep'})
        with patch.object(inputs, '__package__', package.__name__), patch.dict(sys.modules,
                {package.__name__: package, owner.__name__: owner}):
            self.assertTrue(inputs.clear(object(), self.rig, self.action))
            self.assertEqual(self.action, {'artist_note': 'keep'})
            self.action[inputs.RECORD] = 'retained-source'
            owner._export_base_owner = lambda *_args: (_ for _ in ()).throw(ValueError('Source is read-only'))
            with self.assertRaisesRegex(ValueError, 'Source'):
                inputs.clear(object(), self.rig, self.action)
            self.assertEqual(self.action[inputs.RECORD], 'retained-source')

    def test_explicit_clear_rolls_back_partial_metadata_deletion(self):
        package = ModuleType('_clear_rollback_test')
        package.__path__ = []
        owner = ModuleType(package.__name__ + '.animation')
        owner._export_base_owner = lambda *_args: None
        class PartialFailure(Action):
            def __delitem__(self, key):
                if key == inputs.COUNT:
                    raise RuntimeError('Native metadata deletion refused')
                super().__delitem__(key)
        action = PartialFailure({inputs.RECORD: 'saved-base', inputs.COUNT: 2, 'artist_note': 'keep'})
        before = dict(action)
        with patch.object(inputs, '__package__', package.__name__), patch.dict(sys.modules,
                {package.__name__: package, owner.__name__: owner}):
            with self.assertRaisesRegex(RuntimeError, 'deletion refused'):
                inputs.clear(object(), self.rig, action)
        self.assertEqual(action, before)

    def test_nonobject_or_nonfinite_record_is_not_accepted(self):
        for raw in ('[]', '{"sha256":"x","schema":"cdesigner.animation-export-base/1","x":NaN}'):
            with self.subTest(raw=raw):
                self.action[inputs.RECORD] = raw
                with self.assertRaises(ValueError):
                    inputs._read(self.action)

    def test_surface_is_prepared_before_any_action_binding_or_frame_evaluation(self):
        calls = []
        fake_bpy = ModuleType('bpy')
        fake_bpy.data = NS(objects={'Rig': self.rig}, actions={'Custom': self.action})
        fake_bpy.context = NS(scene=NS(frame_set=lambda *_args, **_kw: calls.append('frame')))
        receipt = {'reference_frame': 2.25, 'sha256': 'sealed'}
        fake_inputs = NS(JOB_KEY='base', apply=lambda *_args: (calls.append('apply') or receipt))
        helpers = NS(prepare_animation_snapshot=lambda *_args, **_kw: (calls.append('Dress') or []))
        fake_worker = NS(_dress_snapshot_helpers=lambda: helpers)

        def export(job):
            calls.extend(('Direct', 'Wiggle'))
            fake_worker._dress_snapshot_helpers().prepare_animation_snapshot()
            calls.append('sample')
            return {'warnings': ['Only the selected Action/slot is evaluated; rig NLA is disabled. '
                                 'Unkeyed controls use the snapshot values.']}

        fake_worker.export_job = export
        with patch.dict(sys.modules, {'bpy': fake_bpy}):
            wrapper = load('_test_export_inputs_worker', 'animation_export_inputs_worker.py')
        wrapper._load = lambda _name, filename: fake_inputs if filename == 'animation_export_inputs.py' else fake_worker
        result = wrapper.export_job({'rig': 'Rig', 'action': 'Custom', 'base': {}})
        self.assertEqual(calls, ['Direct', 'Wiggle', 'Dress', 'apply', 'frame', 'sample'])
        self.assertEqual(result['base'], receipt)
        self.assertFalse(any('snapshot values' in value for value in result['warnings']))

    def test_capture_owner_reuses_same_rig_trial_admission_and_protects_source(self):
        package = ModuleType('_capture_guard_test')
        package.__path__ = []
        pose = ModuleType(package.__name__ + '.animation_pose_test')
        pose.SESSION_KEY, pose.REFS_KEY = 'base_session', 'base_refs'
        pose.active = lambda _rig: False
        calls = []
        pose._admission = lambda _context, _rig, **kwargs: calls.append(kwargs)
        self.rig.type, self.rig.library, self.rig.override_library = 'ARMATURE', None, None
        self.rig.mode, self.rig.is_editable = 'OBJECT', True
        self.rig.data = NS(library=None, is_editable=True)
        self.action.asset_data, self.action.override_library, self.action.is_editable = None, None, True
        item = NS(rig=self.rig, side='CUSTOM', custom_action=self.action, composition_json='',
                  new_animation=True, source_action=None)
        saved = NS(workspace_path='workspace', items=[item])
        named = NS(get=lambda _name: self.rig)
        context = NS(scene=NS(objects=named, character_designer_animation_worklist=saved),
                     view_layer=NS(objects=named), screen=None, object=self.rig, window=None)
        modules = {'animation_pose_test': pose,
                   'animation_worklist': NS(current=lambda _context: item, _rigging_module=lambda *_args: None),
                   'animation_rest_pose': NS(is_rest=lambda _rig: False),
                   'body_original_mode': NS(active=lambda _rig: False),
                   'animation_composition': NS(active=lambda _rig: False),
                   'unity_animation': NS(active_preview=lambda _rig: None),
                   'animation_physics': NS(status=lambda _context: {}),
                   'control_pose_blend_ui': NS(_ACTIVE_OPERATORS=[])}
        loaded = {package.__name__: package}
        for name, value in modules.items():
            setattr(package, name, value)
            loaded[package.__name__ + '.' + name] = value
        node = next(node for node in ast.parse((SOURCE / 'animation.py').read_bytes()).body
                    if isinstance(node, ast.FunctionDef) and node.name == '_export_base_owner')
        namespace = {'__package__': package.__name__, '_link_idle': lambda: True}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE / 'animation.py'), 'exec'), namespace)
        owner = namespace['_export_base_owner']
        with patch.dict(sys.modules, loaded):
            self.assertEqual(owner(context, self.rig, self.action), 0)
            self.assertEqual(calls[-1], {'_purpose': 'ANIMATION'})
            item.side = 'SOURCE'
            with self.assertRaisesRegex(ValueError, 'Source'):
                owner(context, self.rig, self.action)
            item.side = 'CUSTOM'
            modules['control_pose_blend_ui']._ACTIVE_OPERATORS = [NS(_session={'rig': self.rig})]
            with self.assertRaisesRegex(ValueError, 'Blend Pose'):
                owner(context, self.rig, self.action)
            modules['control_pose_blend_ui']._ACTIVE_OPERATORS = [NS(_session={'rig': object()})]
            self.assertEqual(owner(context, self.rig, self.action), 0)
            pose._admission = lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError('Forearm calibration'))
            with self.assertRaisesRegex(ValueError, 'Forearm'):
                owner(context, self.rig, self.action)
        self.assertFalse(self.action)


if __name__ == '__main__':
    unittest.main()
