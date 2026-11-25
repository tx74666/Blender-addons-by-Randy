"""Current-pose mapping keeps native ownership checks and strict callers."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
PACKAGE = '_controller_mapping_scope_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + '.controller_animation', ROOT / 'controller_animation.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
mapping_spec = importlib.util.spec_from_file_location(
    PACKAGE + '.animation_controller_mapping', ROOT / 'animation_controller_mapping.py')
mapping = importlib.util.module_from_spec(mapping_spec)
sys.modules[mapping_spec.name] = mapping
mapping_spec.loader.exec_module(mapping)
NS = types.SimpleNamespace


class MappingScopeTests(unittest.TestCase):
    def modules(self):
        modules = {}
        for name in ('root_control', 'torso_controls', 'spine_ik_fk', 'eye_controls'):
            module = types.ModuleType(PACKAGE + '.' + name)
            module.validate = Mock(return_value={'bones': {'control': name}})
            modules[module.__name__] = module
        foot = types.ModuleType(PACKAGE + '.foot_controls')

        def validate(rig, inventory, *, limb_pose_only=False, **kwargs):
            if not rig.owned:
                raise ValueError('Foot native ownership changed')
            if not limb_pose_only and not rig.widget_collection:
                raise ValueError('Foot widget collection missing')
            return {'L': {'bones': {'toe': 'toe_control'}}}

        foot.validate = Mock(side_effect=validate)
        modules[foot.__name__] = foot
        target = types.ModuleType(PACKAGE + '.animation_worklist_target')
        target.control_rest_view = Mock(return_value=None)
        modules[target.__name__] = target
        return modules

    def test_existing_strict_call_still_requires_widget_collection(self):
        modules = self.modules()
        with patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, 'widget collection'):
                controller._controls(NS(owned=True, widget_collection=False), {'bones': []})
        modules[PACKAGE + '.animation_worklist_target'].control_rest_view.assert_called_once()

    def test_pose_scope_checks_each_native_module_without_historical_rest(self):
        modules = self.modules()
        rig, inventory = NS(owned=True, widget_collection=False), {'bones': []}
        with patch.dict(sys.modules, modules):
            names = controller._controls(rig, inventory, pose_matching=True)
        self.assertEqual(names, {'root_control', 'torso_controls', 'spine_ik_fk', 'eye_controls', 'toe_control'})
        modules[PACKAGE + '.animation_worklist_target'].control_rest_view.assert_not_called()
        modules[PACKAGE + '.root_control'].validate.assert_called_once_with(rig, inventory, pose_only=True)
        modules[PACKAGE + '.eye_controls'].validate.assert_called_once_with(rig, inventory, pose_only=True)
        modules[PACKAGE + '.torso_controls'].validate.assert_called_once_with(rig, inventory, pose_only=True)
        modules[PACKAGE + '.spine_ik_fk'].validate.assert_called_once_with(rig, inventory, pose_only=True)

    def test_pose_scope_keeps_native_ownership_refusal(self):
        modules = self.modules()
        with patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(ValueError, 'native ownership'):
                controller._controls(NS(owned=False, widget_collection=False), {'bones': []}, pose_matching=True)

    def test_scope_option_is_an_explicit_boolean(self):
        modules = self.modules()
        with patch.dict(sys.modules, modules):
            for value in (None, 1, 'yes'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    controller._controls(NS(), {'bones': []}, pose_matching=value)


class WorkerBoundaryTests(unittest.TestCase):
    def test_public_build_delegates_without_native_scene_evaluation(self):
        worker = types.ModuleType(PACKAGE + '.animation_controller_worker')
        expected = object()
        worker.build = Mock(return_value=expected)
        inputs = tuple(object() for _ in range(5))
        with patch.dict(sys.modules, {worker.__name__: worker}):
            result = mapping.build(*inputs, source_fps=29.97)
        self.assertIs(result, expected)
        worker.build.assert_called_once_with(*inputs, source_fps=29.97)

    def test_native_worker_entry_refuses_foreground_before_any_scene_work(self):
        bpy = types.ModuleType('bpy')
        bpy.app = NS(background=False)
        with patch.dict(sys.modules, {'bpy': bpy}):
            with self.assertRaisesRegex(RuntimeError, 'independent background worker'):
                mapping._build_isolated(None, None, None, None, None)


class VerificationTimeTests(unittest.TestCase):
    def test_expansion_keeps_original_strict_samples_and_original_quarters(self):
        native = types.ModuleType(PACKAGE + '.controller_animation')
        scene = NS(frame=0.)
        native._frame = lambda scene, frame: setattr(scene, 'frame', frame)
        native._probes = controller._probes
        native._quality_policy = Mock(return_value={'scene_meters_per_unit': 1.})
        native._pose = Mock(return_value=object())
        native._error = lambda actual, expected: {'matrix': 1., 'frame': scene.frame}
        native._world_playback_error = lambda *args: {'position_meters': 0., 'rotation_radians': 0.}
        native._acceptable = Mock(side_effect=lambda error: error['frame'] in (1., 2.))
        native._playback_acceptable = Mock(return_value=True)
        plan = NS(source_times=(1., 2.), target_world=object(), names=('bone',),
                  diagnostics={'source_fps': 24., 'target_fps': 24.})
        target = NS(animation_data_create=lambda: NS())
        action = NS(slots=[NS(handle=1)])
        context = NS(scene=scene, view_layer=NS(update=lambda: None))
        progress = Mock()
        with patch.dict(sys.modules, {native.__name__: native}), patch.object(mapping, '_desired', return_value={}):
            failed, proof = mapping._verify_mapping(context, object(), target, plan, action, 1,
                                                    (1., 1.25, 2.), strict_times=(1., 2.), progress=progress)
        count = len(proof['verification_source_times'])
        self.assertEqual([event.args for event in progress.call_args_list],
                         [('Checking final motion', index, count, 0) for index in range(1, count + 1)])
        self.assertFalse(failed)
        self.assertEqual(native._acceptable.call_count, 2)
        self.assertEqual(proof['strict_source_times'], [1., 2.])
        self.assertTrue({1.25, 1.5, 1.75} <= set(proof['verification_source_times']))

    def test_fractional_adaptive_times_use_source_fps_and_source_origin(self):
        plan = NS(source_times=(10.25, 11.05),
                  diagnostics={'source_fps': 24., 'target_fps': 60.})
        times = mapping._verification_times(plan, plan.source_times, (1., 1.625, 3.))
        self.assertEqual(times, (10.25, 10.5, 11.05))
        self.assertEqual(controller._probes(times),
                         (10.25, 10.3125, 10.375, 10.4375, 10.5,
                          10.6375, 10.775, 10.912500000000001, 11.05))

    def test_inverse_mapping_retains_fractional_samples_at_slower_target_fps(self):
        plan = NS(source_times=(2.5, 4.5),
                  diagnostics={'source_fps': 60., 'target_fps': 24.})
        self.assertEqual(mapping._verification_times(plan, (2.5, 3.25, 4.5), (1., 1.3, 1.8)),
                         (2.5, 3.25, 4.5))

    def test_missing_or_nonfinite_native_grid_is_rejected(self):
        plan = NS(source_times=(1., 2.),
                  diagnostics={'source_fps': 24., 'target_fps': 30.})
        for times in ((), (float('nan'),), (float('inf'),), (True,)):
            with self.subTest(times=times), self.assertRaises(mapping.MappingNeeded):
                mapping._verification_times(plan, plan.source_times, times)


class CurrentWorldTests(unittest.TestCase):
    def test_raw_basis_is_authoritative_and_existing_transform_gate_is_reused(self):
        retarget = types.ModuleType(PACKAGE + '.animation_retarget')
        retarget._uniform_world = Mock(side_effect=lambda value: value.matrix_world)
        rig = NS(name='source', parent=None, constraints=[], matrix_basis=[[.75, 0.], [0., .75]],
                 matrix_world=[[1., 0.], [0., 1.]], data=NS(bones=[]))
        with patch.dict(sys.modules, {retarget.__name__: retarget}):
            self.assertEqual(mapping._current_world(rig), rig.matrix_basis)
            before = mapping._rest_fingerprint(rig)
            rig.matrix_world = [[2., 0.], [0., 2.]]
            self.assertEqual(mapping._rest_fingerprint(rig), before)
            rig.parent = object()
            with self.assertRaises(mapping.MappingNeeded):
                mapping._current_world(rig)
        self.assertEqual(retarget._uniform_world.call_count, 1)


if __name__ == '__main__':
    unittest.main()
