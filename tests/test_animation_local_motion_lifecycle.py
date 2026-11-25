"""Local-motion startup/cleanup and preview context regressions without Blender.

Execute the actual product definitions through AST with owned-process, timer,
RNA and importer doubles. No model, service, Blender process or GUI is started.
CD_LOCAL_MOTION_TEST_SOURCE permits an immutable approved-source red run.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(os.environ.get('CD_LOCAL_MOTION_TEST_SOURCE',
    Path(__file__).resolve().parents[1] / 'addons/character_designer/animation.py'))


class Timers:
    def __init__(self):
        self.registered = set()
        self.mode = 'normal'
        self.unregister = Mock(side_effect=self._unregister)

    def is_registered(self, callback):
        return callback in self.registered

    def register(self, callback, **kwargs):
        if self.mode == 'before':
            raise RuntimeError('poll registration rejected before insertion')
        self.registered.add(callback)
        if self.mode == 'after':
            raise RuntimeError('poll registration rejected after insertion')

    def _unregister(self, callback):
        self.registered.remove(callback)


class Settings(NS):
    def __init__(self):
        super().__init__(_fault=None, prompt='Walk', runtime_root='configured-runtime',
            duration=2., seed=42, status='Previous ready result', has_error=False,
            result_path='previous-usable.bvh', log_path='previous.log', source=object())

    def __setattr__(self, name, value):
        fault = getattr(self, '_fault', None)
        if fault and fault[0] == name:
            super().__setattr__('_fault', None)
            if fault[1]:
                super().__setattr__(name, value)
            raise RuntimeError('RNA assignment failed: ' + name)
        super().__setattr__(name, value)


class Job:
    def __init__(self):
        self.log_path = Path('owned-backend.log')
        self.status, self.error = 'Starting local generation', ''
        self.result_path = 'new-complete-result.bvh'
        self.cancel_error = self.wait_error = None
        self.returncode = None
        self.cancel = Mock(side_effect=self._cancel)
        self.poll = Mock(return_value=False)
        self.process = NS(wait=Mock(side_effect=self._wait), poll=lambda: self.returncode)

    def _cancel(self):
        if self.cancel_error:
            raise self.cancel_error

    def _wait(self, *, timeout):
        if self.wait_error:
            raise self.wait_error
        self.returncode = -15
        return self.returncode


class Harness:
    def __init__(self):
        self.tree = ast.parse(SOURCE.read_bytes())
        self.settings, self.timers, self.job = Settings(), Timers(), Job()
        self.manager = NS(character_designer_animation=self.settings)
        self.context = NS(window_manager=self.manager)
        self.operator = NS(report=Mock())
        self.factory = Mock(return_value=self.job)
        self.ns = dict(_job=None, _job_window_manager=None, LocalMotionJob=self.factory,
            _settings=lambda context: context.window_manager.character_designer_animation,
            _redraw=Mock(), bpy=NS(path=NS(abspath=lambda path: path),
                                  app=NS(timers=self.timers)))
        # The old source has no cleanup helper. Its real operators still run in
        # the red suite, exposing the original leak instead of a missing-helper
        # failure manufactured by the harness.
        functions = [node for node in self.tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {'_poll_job', '_cancel_local_motion'}]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        for class_name, alias in (('CHARACTERDESIGNER_OT_animation_generate', 'generate'),
                                  ('CHARACTERDESIGNER_OT_animation_cancel', 'cancel')):
            cls = next(node for node in self.tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
            execute = deepcopy(next(node for node in cls.body
                                    if isinstance(node, ast.FunctionDef) and node.name == 'execute'))
            execute.name = alias
            exec(compile(ast.Module(body=[execute], type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def generate(self):
        return self.ns['generate'](self.operator, self.context)

    def cancel(self):
        return self.ns['cancel'](self.operator, self.context)

    def attach(self):
        self.ns['_job'], self.ns['_job_window_manager'] = self.job, self.manager
        self.timers.registered.add(self.ns['_poll_job'])


class LocalMotionLifecycleTests(unittest.TestCase):
    def assert_released(self, h):
        self.assertIsNone(h.ns['_job'])
        self.assertIsNone(h.ns['_job_window_manager'])
        self.assertFalse(h.timers.is_registered(h.ns['_poll_job']))
        h.job.cancel.assert_called_once_with()
        h.job.process.wait.assert_called_once_with(timeout=5)

    def test_existing_job_or_poll_refuses_before_launching_another_worker(self):
        for existing in ('job', 'timer'):
            with self.subTest(existing=existing):
                h = Harness()
                previous = Job()
                if existing == 'job':
                    h.ns['_job'], h.ns['_job_window_manager'] = previous, h.manager
                else:
                    h.timers.registered.add(h.ns['_poll_job'])
                self.assertEqual(h.generate(), {'CANCELLED'})
                h.factory.assert_not_called()
                self.assertIs(h.ns['_job'], previous if existing == 'job' else None)
                self.assertEqual(h.settings.result_path, 'previous-usable.bvh')
                previous.cancel.assert_not_called()

    def test_registration_fault_before_or_after_insertion_stops_only_owned_job(self):
        for mode in ('before', 'after'):
            with self.subTest(mode=mode):
                h = Harness()
                previous_source = h.settings.source
                h.timers.mode = mode
                self.assertEqual(h.generate(), {'CANCELLED'})
                self.assert_released(h)
                self.assertEqual(h.settings.result_path, 'previous-usable.bvh')
                self.assertIs(h.settings.source, previous_source)
                self.assertTrue(h.settings.has_error)
                self.assertIn('poll', h.settings.status)
                self.assertIn('stopped', h.settings.status)
                self.assertEqual(h.timers.unregister.call_count, int(mode == 'after'))
                # Successful rollback makes a normal retry possible.
                h.timers.mode = 'normal'
                self.assertEqual(h.generate(), {'FINISHED'})
                self.assertIs(h.ns['_job'], h.job)
                self.assertEqual(h.settings.result_path, '')

    def test_startup_settings_fault_retains_previous_result_and_preview(self):
        for field in ('log_path', 'status', 'has_error', 'result_path'):
            for after_write in (False, True):
                with self.subTest(field=field, after_write=after_write):
                    h = Harness()
                    previous_source = h.settings.source
                    h.settings._fault = (field, after_write)
                    self.assertEqual(h.generate(), {'CANCELLED'})
                    self.assert_released(h)
                    self.assertEqual(h.settings.result_path, 'previous-usable.bvh')
                    self.assertIs(h.settings.source, previous_source)
                    self.assertTrue(h.settings.has_error)
                    self.assertIn('RNA assignment failed', h.settings.status)

    def test_registration_cleanup_failure_keeps_owner_for_cancel_and_retry(self):
        for failure in ('cancel', 'wait'):
            with self.subTest(failure=failure):
                h = Harness()
                previous_source = h.settings.source
                h.timers.mode = 'after'
                if failure == 'cancel':
                    h.job.cancel_error = RuntimeError('owned worker could not be signalled')
                else:
                    h.job.wait_error = subprocess.TimeoutExpired('owned-worker', 5)
                self.assertEqual(h.generate(), {'CANCELLED'})
                self.assertIs(h.ns['_job'], h.job)
                self.assertIs(h.ns['_job_window_manager'], h.manager)
                self.assertFalse(h.timers.is_registered(h.ns['_poll_job']))
                self.assertEqual(h.settings.result_path, 'previous-usable.bvh')
                self.assertIs(h.settings.source, previous_source)
                self.assertIn('Cancel Generation', h.settings.status)
                self.assertEqual(h.generate(), {'CANCELLED'})
                self.assertEqual(h.factory.call_count, 1)
                h.job.cancel_error = h.job.wait_error = None
                self.assertEqual(h.cancel(), {'FINISHED'})
                self.assertIsNone(h.ns['_job'])
                self.assertIsNone(h.ns['_job_window_manager'])
                self.assertFalse(h.settings.has_error)
                h.timers.mode = 'normal'
                self.assertEqual(h.generate(), {'FINISHED'})

    def test_explicit_cancel_waits_for_exit_before_releasing_owner(self):
        h = Harness()
        h.attach()
        self.assertEqual(h.cancel(), {'FINISHED'})
        self.assert_released(h)
        self.assertEqual(h.settings.result_path, 'previous-usable.bvh')
        self.assertFalse(h.settings.has_error)

    def test_explicit_cancel_failure_keeps_worker_and_poll_owned(self):
        for failure in ('cancel', 'wait'):
            with self.subTest(failure=failure):
                h = Harness()
                h.attach()
                if failure == 'cancel':
                    h.job.cancel_error = RuntimeError('cancel failed')
                else:
                    h.job.wait_error = subprocess.TimeoutExpired('owned-worker', 5)
                self.assertEqual(h.cancel(), {'CANCELLED'})
                self.assertIs(h.ns['_job'], h.job)
                self.assertIs(h.ns['_job_window_manager'], h.manager)
                self.assertTrue(h.timers.is_registered(h.ns['_poll_job']))
                self.assertTrue(h.settings.has_error)
                self.assertIn('Cancel Generation', h.settings.status)
                h.job.cancel_error = h.job.wait_error = None
                self.assertEqual(h.cancel(), {'FINISHED'})
                self.assertIsNone(h.ns['_job'])
                self.assertFalse(h.timers.is_registered(h.ns['_poll_job']))

    def test_success_tracks_until_poll_completion_then_keeps_new_result(self):
        h = Harness()
        previous_source = h.settings.source
        self.assertEqual(h.generate(), {'FINISHED'})
        self.assertIs(h.ns['_job'], h.job)
        self.assertIs(h.ns['_job_window_manager'], h.manager)
        self.assertTrue(h.timers.is_registered(h.ns['_poll_job']))
        self.assertEqual(h.settings.result_path, '')
        self.assertIs(h.settings.source, previous_source)
        h.job.poll.side_effect = (False, True)
        self.assertEqual(h.ns['_poll_job'](), .5)
        self.assertIs(h.ns['_job'], h.job)
        self.assertIsNone(h.ns['_poll_job']())
        self.assertIsNone(h.ns['_job'])
        self.assertIsNone(h.ns['_job_window_manager'])
        self.assertEqual(h.settings.result_path, h.job.result_path)
        self.assertFalse(h.settings.has_error)
        h.job.cancel.assert_not_called()
        h.job.process.wait.assert_not_called()


class ImportObject(dict):
    __hash__ = object.__hash__

    def __init__(self, name, mode='OBJECT', selected=False):
        super().__init__(author='retained')
        self.name, self.mode, self.selected = name, mode, selected
        self.type = 'ARMATURE'

    def select_set(self, value):
        self.selected = value


class ImportCollection(list):
    def remove(self, obj, **kwargs):
        super().remove(obj)


class PreviewContext:
    def __init__(self, data, scene, active):
        self.data, self.scene = data, scene
        class ViewLayerObjects:
            def __init__(self):
                self.active = active

            def __contains__(self, name):
                return any(obj.name == name for obj in data.objects)
        self.view_layer = NS(objects=ViewLayerObjects())

    @property
    def object(self):
        return self.view_layer.objects.active

    @property
    def selected_objects(self):
        return [obj for obj in self.data.objects if obj.selected]


class ImportPreviewContextTests(unittest.TestCase):
    def test_success_and_failure_preserve_fractional_frame_and_context(self):
        for fail in (False, True):
            with self.subTest(import_failure=fail):
                tree = ast.parse(SOURCE.read_bytes())
                fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                          and node.name == 'import_motion_preview')
                author = ImportObject('Artist Rig', 'POSE', True)
                author['weights'] = [1., .25]
                before_author = deepcopy(dict(author))
                data = NS(objects=ImportCollection([author]), armatures=ImportCollection(),
                          actions=ImportCollection())
                scene = NS(frame_current=12, frame_subframe=.375)

                def frame_set(frame, subframe=0.):
                    scene.frame_current, scene.frame_subframe = frame, subframe

                scene.frame_set = Mock(side_effect=frame_set)
                context = PreviewContext(data, scene, author)

                def mode_set(*, mode):
                    context.object.mode = mode

                def load(ctx, **kwargs):
                    source = ImportObject('Imported', selected=True)
                    data.objects.append(source)
                    author.select_set(False)
                    context.view_layer.objects.active = source
                    frame_set(77, subframe=.625)
                    if fail:
                        raise RuntimeError('test BVH importer refused file')

                modules = {name: ModuleType(name)
                           for name in ('io_anim_bvh', 'bpy_extras', 'bpy_extras.io_utils')}
                modules['io_anim_bvh'].import_bvh = NS(load=load)
                modules['bpy_extras.io_utils'].axis_conversion = lambda **kwargs: NS(to_4x4=lambda: None)
                ns = dict(bpy=NS(data=data, ops=NS(object=NS(mode_set=mode_set))))
                exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec'), ns)
                with patch.dict(sys.modules, modules):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'BVH importer refused'):
                            ns['import_motion_preview'](context, 'motion.bvh', 1)
                    else:
                        source = ns['import_motion_preview'](context, 'motion.bvh', 1)
                        self.assertEqual(source.name, 'Kimodo Preview')
                        self.assertTrue(source['character_designer_motion_preview'])
                        self.assertFalse(source.selected)
                self.assertEqual((scene.frame_current, scene.frame_subframe), (12, .375))
                self.assertIs(context.view_layer.objects.active, author)
                self.assertEqual(author.mode, 'POSE')
                self.assertEqual(context.selected_objects, [author])
                self.assertEqual(dict(author), before_author)
                self.assertEqual(len(data.objects), 1 if fail else 2)


if __name__ == '__main__':
    unittest.main()
