"""Disposable baking defers the real corrective guard through clone cleanup."""

import ast
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class BakeRuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        name = '_bake_runtime_guard_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        self.events = []
        self.forearm = ModuleType(name + '.forearm_twist')
        self.forearm._BUSY = False
        self.runtime_update = Mock(side_effect=lambda _scene: self.events.append(
            ('runtime_flush', self.forearm._BUSY)))
        self.forearm.update_runtime = self.runtime_update
        self.forearm.contextmanager = contextmanager
        tree = ast.parse((SOURCE / 'forearm_twist.py').read_text(encoding='utf-8'))
        guard = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == 'defer_runtime')
        exec(compile(ast.Module(body=[guard], type_ignores=[]), 'forearm_twist.py', 'exec'),
             self.forearm.__dict__)
        real_guard = self.forearm.defer_runtime

        @contextmanager
        def observe_guard(context, *, flush_on_exit=True):
            self.events.append(('guard_enter', flush_on_exit))
            try:
                with real_guard(context, flush_on_exit=flush_on_exit) as refresh:
                    yield refresh
            finally:
                self.events.append(('guard_exit', self.forearm._BUSY))

        self.forearm.defer_runtime = observe_guard
        ua = ModuleType(name + '.unity_animation')
        ua.UnityAnimationError = type('UnityAnimationError', (ValueError,), {})
        ua._swap_armature_data = lambda *_args, **_kwargs: self.mark('copy_swap')
        package.forearm_twist, package.unity_animation = self.forearm, ua
        bpy = ModuleType('bpy')
        bpy.data = NS(objects=NS(remove=lambda *_args, **_kwargs: self.mark('clone_removed')),
                      armatures=NS(remove=lambda *_args, **_kwargs: self.mark('data_removed')))
        mathutils = ModuleType('mathutils')
        mathutils.Matrix = Mock()
        spec = importlib.util.spec_from_file_location(
            name + '.unity_animation_controls', SOURCE / 'unity_animation_controls.py')
        self.controls = importlib.util.module_from_spec(spec)
        environment = patch.dict(sys.modules, {
            name: package, self.forearm.__name__: self.forearm,
            ua.__name__: ua, spec.name: self.controls, 'bpy': bpy, 'mathutils': mathutils})
        environment.start()
        self.addCleanup(environment.stop)
        spec.loader.exec_module(self.controls)
        self.error_type = ua.UnityAnimationError
        self.data_copy = NS(users=0, use_fake_user=True)
        self.original_data = NS(bones={}, copy=lambda: self.data_copy)
        self.clone = NS(data=self.original_data, animation_data=None, pose=NS(bones=[]),
                        update_tag=lambda **_kwargs: self.mark('clone_update'))
        self.target = NS(data=self.original_data, pose=NS(bones=[]),
                         copy=lambda: self.clone)
        self.scene = NS(collection=NS(objects=NS(link=lambda _clone: self.mark('clone_linked'))),
                        frame_current=17)
        self.context = NS(scene=self.scene, view_layer=NS(update=self.scene_update))
        # No geometry is needed to exercise the actual core's clone lifecycle.
        # Native math and pose correctness remain covered by the Blender fixture.
        self.plan = self.controls.ControlPlan(frozenset(), frozenset(), frozenset(),
            frozenset(), {}, {}, frozenset(), {})

    def mark(self, event):
        self.events.append((event, self.forearm._BUSY))

    def scene_update(self):
        self.mark('scene_update')
        # Corrective handlers return immediately while the existing guard is busy.
        if not self.forearm._BUSY:
            self.runtime_update(self.scene)

    def assert_cleanup_guarded(self):
        for event in ('clone_linked', 'copy_swap', 'clone_removed', 'data_removed'):
            self.assertIn((event, True), self.events)
        self.assertLess(self.events.index(('clone_removed', True)),
                        self.events.index(('data_removed', True)))
        self.assertLess(self.events.index(('data_removed', True)),
                        self.events.index(('guard_exit', False)))
        self.assertIs(self.target.data, self.original_data)
        self.assertEqual(self.scene.frame_current, 17)
        self.assertFalse(self.forearm._BUSY)
        self.runtime_update.assert_not_called()

    def test_success_cleans_clone_before_guard_exit_without_flushing_artist(self):
        result = self.controls.bake_channels(self.context, self.target, self.plan, [{}])
        self.assertEqual(result.sample_count, 1)
        self.assertEqual(result.channels, {})
        self.assertIn(('guard_enter', False), self.events)
        self.assert_cleanup_guarded()

    def test_sample_failure_cleans_clone_and_restores_guard_state(self):
        with self.assertRaisesRegex(self.error_type, 'does not cover'):
            self.controls.bake_channels(self.context, self.target, self.plan, [{'foreign': None}])
        self.assert_cleanup_guarded()

    def test_preflight_failure_does_not_leave_runtime_deferred(self):
        with self.assertRaisesRegex(self.error_type, 'verified control plan'):
            self.controls.bake_channels(self.context, self.target, None, [{}])
        self.assertFalse(self.forearm._BUSY)
        self.assertNotIn(('clone_linked', True), self.events)
        self.runtime_update.assert_not_called()

    def test_nested_guard_preserves_outer_state_and_its_single_final_flush(self):
        with self.forearm.defer_runtime(self.context):
            self.assertTrue(self.forearm._BUSY)
            self.controls.bake_channels(self.context, self.target, self.plan, [{}])
            self.assertTrue(self.forearm._BUSY)
            self.assertIn(('guard_exit', True), self.events)
            self.runtime_update.assert_not_called()
        self.assertFalse(self.forearm._BUSY)
        self.runtime_update.assert_called_once_with(self.scene)
        self.assertEqual(self.events[-2:], [('runtime_flush', False), ('guard_exit', False)])


if __name__ == '__main__':
    unittest.main()
