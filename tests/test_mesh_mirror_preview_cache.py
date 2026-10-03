"""Transient mirror-preview GPU resource lifetime, without a Blender process."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class MirrorPreviewCache(unittest.TestCase):
    def setUp(self):
        package_name = '_mirror_preview_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        props = ModuleType('bpy.props')
        for name in ('BoolProperty', 'FloatProperty', 'IntProperty', 'PointerProperty', 'StringProperty'):
            setattr(props, name, lambda **kwargs: kwargs)
        types = ModuleType('bpy.types')
        types.Operator, types.PropertyGroup, types.Object = object, object, object
        types.Scene = type('Scene', (), {})
        types.SpaceView3D = SimpleNamespace(draw_handler_add=Mock(side_effect=lambda *args: object()),
                                           draw_handler_remove=Mock())
        handlers = ModuleType('bpy.app.handlers')
        handlers.persistent = lambda fn: fn
        handlers.load_pre, handlers.undo_pre, handlers.redo_pre = [], [], []
        app = ModuleType('bpy.app')
        app.handlers, app.background = handlers, False
        bpy.app, bpy.props, bpy.types = app, props, types
        self.obj = SimpleNamespace(mode='EDIT', matrix_world=(1, 0, 0, 1))
        bpy.context = SimpleNamespace(edit_object=self.obj,
                                      window_manager=SimpleNamespace(windows=[]))
        mathutils = ModuleType('mathutils')
        mathutils.Vector = Mock()
        mirror = ModuleType(package_name + '.mesh_mirror')
        mirror._matrix_tuple = tuple
        topology = ModuleType(package_name + '.topology_symmetry')
        topology._select_result_region = Mock()
        self.gpu = ModuleType('gpu')
        self.gpu.shader = SimpleNamespace(from_builtin=Mock(side_effect=lambda name: SimpleNamespace(
            bind=Mock(), uniform_float=Mock())))
        self.gpu.state = SimpleNamespace(
            depth_test_get=Mock(return_value='LESS_EQUAL'), line_width_get=Mock(return_value=4.5),
            depth_test_set=Mock(), line_width_set=Mock())
        extras = ModuleType('gpu_extras')
        batch = ModuleType('gpu_extras.batch')
        batch.batch_for_shader = Mock(side_effect=lambda *args: SimpleNamespace(draw=Mock()))
        extras.batch = batch
        self.make_batch = batch.batch_for_shader
        modules = patch.dict(sys.modules, {
            package_name: package, 'bpy': bpy, 'bpy.props': props, 'bpy.types': types,
            'bpy.app': app, 'bpy.app.handlers': handlers, 'mathutils': mathutils,
            mirror.__name__: mirror, topology.__name__: topology,
            'gpu': self.gpu, 'gpu_extras': extras, 'gpu_extras.batch': batch,
        })
        modules.start()
        self.addCleanup(modules.stop)
        name = package_name + '.mesh_mirror_ui'
        spec = importlib.util.spec_from_file_location(name, SOURCE / 'mesh_mirror_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.ui
        spec.loader.exec_module(self.ui)
        self.bpy = bpy
        self.plan = SimpleNamespace(obj=self.obj, reference=None)
        self.color = (1.0, 0.5, 0.0, 1.0)
        self.positions = ((0, 0, 0), (1, 0, 0))
        self.ui.preview_geometry = Mock(return_value=(
            [(self.color, self.positions), (self.color, ())], []))
        self.ui._selection_signature = Mock(return_value=('original selection',))

    def show(self):
        self.ui.show_preview(self.plan)
        return self.ui._preview

    def assert_draw_state_restored(self):
        self.gpu.state.depth_test_set.assert_called_with('LESS_EQUAL')
        self.gpu.state.line_width_set.assert_called_with(4.5)

    def test_resources_are_lazy_and_reused_for_unchanged_preview(self):
        self.ui._draw_lines()
        data = self.show()
        self.gpu.shader.from_builtin.assert_not_called()
        self.make_batch.assert_not_called()
        for _ in range(10):
            self.ui._draw_lines()
        self.gpu.shader.from_builtin.assert_called_once_with('UNIFORM_COLOR')
        self.make_batch.assert_called_once_with(data['gpu_shader'], 'LINES', {'pos': self.positions})
        shader, batch = data['gpu_shader'], data['gpu_batches'][0][1]
        self.assertEqual(shader.bind.call_count, 10)
        self.assertEqual(batch.draw.call_count, 10)
        batch.draw.assert_called_with(shader)
        shader.uniform_float.assert_called_with('color', self.color)
        self.assert_draw_state_restored()

    def test_replacement_gets_its_own_shader_and_batches(self):
        first = self.show()
        self.ui._draw_lines()
        second = self.show()
        self.assertIsNot(first, second)
        self.assertNotIn('gpu_shader', second)
        self.assertNotIn('gpu_batches', second)
        self.ui._draw_lines()
        self.assertIsNot(first['gpu_shader'], second['gpu_shader'])
        self.assertIsNot(first['gpu_batches'][0][1], second['gpu_batches'][0][1])
        self.assertEqual(self.gpu.shader.from_builtin.call_count, 2)
        self.assertEqual(self.make_batch.call_count, 2)

    def test_geometry_invalidation_drops_preview_and_retained_resources(self):
        self.show()
        self.ui._draw_lines()
        self.ui._selection_signature.return_value = ('changed selection',)
        self.assertIsNone(self.ui._valid_preview(force=True))
        self.assertIsNone(self.ui._preview)
        self.ui._draw_lines()
        self.assertEqual(self.gpu.shader.from_builtin.call_count, 1)
        self.assertEqual(self.make_batch.call_count, 1)

    def test_runtime_invalidation_and_unregister_clear_resource_owner(self):
        self.ui.register_mesh_mirror_runtime()
        self.ui.register_mesh_mirror_runtime()
        self.assertEqual(len(self.ui._handlers), 2)
        for handlers in (self.bpy.app.handlers.load_pre, self.bpy.app.handlers.undo_pre,
                         self.bpy.app.handlers.redo_pre):
            self.assertEqual(handlers, [self.ui._invalidate_preview])
            self.show()
            self.ui._draw_lines()
            handlers[0]()
            self.assertIsNone(self.ui._preview)
        self.show()
        self.ui._draw_lines()
        self.ui.unregister_mesh_mirror_runtime()
        self.assertIsNone(self.ui._preview)
        self.assertEqual(self.ui._handlers, [])
        self.assertEqual(self.bpy.types.SpaceView3D.draw_handler_remove.call_count, 2)
        self.assertFalse(hasattr(self.bpy.types.Scene, 'character_designer_mesh_mirror'))
        self.assertEqual(self.bpy.app.handlers.load_pre, [])
        self.assertEqual(self.bpy.app.handlers.undo_pre, [])
        self.assertEqual(self.bpy.app.handlers.redo_pre, [])

    def test_failed_shader_creation_does_not_poison_next_draw(self):
        data = self.show()
        factory = self.gpu.shader.from_builtin.side_effect
        self.gpu.shader.from_builtin.side_effect = RuntimeError('shader unavailable')
        with self.assertRaisesRegex(RuntimeError, 'shader unavailable'):
            self.ui._draw_lines()
        self.assertNotIn('gpu_shader', data)
        self.assertNotIn('gpu_batches', data)
        self.gpu.state.depth_test_set.assert_not_called()
        self.gpu.state.line_width_set.assert_not_called()
        self.gpu.shader.from_builtin.side_effect = factory
        self.ui._draw_lines()
        self.assertIn('gpu_shader', data)
        self.assertIn('gpu_batches', data)
        self.assert_draw_state_restored()

    def test_batch_creation_failure_restores_state_and_can_retry(self):
        data = self.show()
        factory = self.make_batch.side_effect
        self.make_batch.side_effect = RuntimeError('batch unavailable')
        with self.assertRaisesRegex(RuntimeError, 'batch unavailable'):
            self.ui._draw_lines()
        self.assertNotIn('gpu_shader', data)
        self.assertNotIn('gpu_batches', data)
        self.assert_draw_state_restored()
        self.make_batch.side_effect = factory
        self.ui._draw_lines()
        self.assertEqual(self.gpu.shader.from_builtin.call_count, 2)
        self.assertIn('gpu_batches', data)
        self.assert_draw_state_restored()

    def test_retained_batch_draw_failure_still_restores_state(self):
        data = self.show()
        self.ui._draw_lines()
        data['gpu_batches'][0][1].draw.side_effect = RuntimeError('draw unavailable')
        with self.assertRaisesRegex(RuntimeError, 'draw unavailable'):
            self.ui._draw_lines()
        self.assertNotIn('gpu_shader', data)
        self.assertNotIn('gpu_batches', data)
        self.gpu.shader.from_builtin.assert_called_once_with('UNIFORM_COLOR')
        self.assert_draw_state_restored()

    def test_failed_cached_shader_is_replaced_on_next_healthy_draw(self):
        data = self.show()
        self.ui._draw_lines()
        failed_shader, failed_batch = data['gpu_shader'], data['gpu_batches'][0][1]
        failed_shader.uniform_float.side_effect = RuntimeError('shader context lost')
        with self.assertRaisesRegex(RuntimeError, 'shader context lost'):
            self.ui._draw_lines()
        self.assertNotIn('gpu_shader', data)
        self.assertNotIn('gpu_batches', data)
        self.assert_draw_state_restored()
        self.ui._draw_lines()
        self.assertIsNot(data['gpu_shader'], failed_shader)
        self.assertIsNot(data['gpu_batches'][0][1], failed_batch)
        self.assertEqual(self.gpu.shader.from_builtin.call_count, 2)
        self.assertEqual(self.make_batch.call_count, 2)
        data['gpu_batches'][0][1].draw.assert_called_once_with(data['gpu_shader'])
        self.assert_draw_state_restored()


if __name__ == '__main__':
    unittest.main()
