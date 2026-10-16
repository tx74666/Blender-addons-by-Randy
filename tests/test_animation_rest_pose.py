"""Display ownership/rollback tests with fake RNA, without launching Blender."""

import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class ID(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


class ObjectMap(dict):
    active = None


class RestPoseTests(unittest.TestCase):
    def setUp(self):
        self.name = '_rest_display_' + uuid.uuid4().hex
        self.package = ModuleType(self.name)
        self.package.__path__ = [str(SOURCE)]
        self.ua = ModuleType(self.name + '.unity_animation')
        self.ua._set_playing = Mock()
        self.bpy = NS(data=NS(objects=[]))
        modules = patch.dict(sys.modules, {self.name: self.package, 'bpy': self.bpy,
                                          self.ua.__name__: self.ua})
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location(self.name + '.animation_rest_pose',
                                                      SOURCE / 'animation_rest_pose.py')
        self.rest = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.rest
        spec.loader.exec_module(self.rest)
        self.package.animation_rest_pose = self.rest
        self.package.unity_animation = self.ua
        self.data = NS(name_full='Artist Rig', library=None, is_editable=True,
                       pose_position='POSE', bones={'rest': ('untouched',)})
        self.action = ID()
        self.action['keys'] = [('location', 1, 2.0)]
        self.rig = ID(name='Main', type='ARMATURE', mode='OBJECT', library=None,
                      override_library=None, is_editable=True, data=self.data,
                      animation_data=NS(action=self.action, action_slot=17),
                      pose=NS(channels=['artist pose']))
        self.mesh = NS(name='Shoes', type='MESH', mode='EDIT', selected_vertices=[1, 7])
        self.saved = NS(rig=self.rig)
        objects = ObjectMap({self.rig.name: self.rig, self.mesh.name: self.mesh})
        self.context = NS(scene=NS(character_designer_setup=NS(rig=self.rig),
                                  character_designer_animation_worklist=self.saved,
                                  objects=objects),
                          view_layer=NS(objects=objects, update=Mock()), object=self.mesh)
        self.bpy.data.objects.extend([self.rig, self.mesh])

    def assert_artist_untouched(self):
        self.assertEqual(self.mesh.mode, 'EDIT')
        self.assertIs(self.context.object, self.mesh)
        self.assertEqual(self.mesh.selected_vertices, [1, 7])
        self.assertIs(self.rig.data, self.data)
        self.assertIs(self.rig.animation_data.action, self.action)
        self.assertEqual(self.rig.animation_data.action_slot, 17)
        self.assertEqual(self.action['keys'], [('location', 1, 2.0)])
        self.assertEqual(self.rig.pose.channels, ['artist pose'])
        self.assertEqual(self.data.bones, {'rest': ('untouched',)})

    def test_mesh_edit_rest_and_pose_preserve_artist_data_and_mode(self):
        self.assertTrue(self.rest.can_show(self.context))
        self.assertIs(self.rest.show_rest(self.context), self.rig)
        self.assertTrue(self.rest.is_rest(self.rig))
        self.assertIsInstance(self.rig[self.rest.PREVIEW_KEY], str)
        self.assert_artist_untouched()
        self.assertIs(self.rest.show_pose(self.context), self.rig)
        self.assertFalse(self.rest.is_rest(self.rig))
        self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)
        self.assert_artist_untouched()
        self.assertEqual(self.ua._set_playing.call_count, 2)

    def test_shared_armature_data_is_rejected_without_copying_or_changing_it(self):
        self.bpy.data.objects.append(NS(type='ARMATURE', data=self.data))
        self.assertFalse(self.rest.can_show(self.context))
        with self.assertRaisesRegex(ValueError, 'shares'):
            self.rest.show_rest(self.context)
        self.assertEqual(self.data.pose_position, 'POSE')
        self.assert_artist_untouched()

    def test_conflicting_worklist_rig_is_rejected(self):
        self.saved.rig = NS(type='ARMATURE')
        with self.assertRaisesRegex(ValueError, 'different rigs'):
            self.rest.show_rest(self.context)
        self.assertEqual(self.data.pose_position, 'POSE')

    def test_armature_edit_is_rejected(self):
        self.rig.mode = 'EDIT'
        self.assertFalse(self.rest.can_show(self.context))
        with self.assertRaisesRegex(ValueError, 'Armature Edit'):
            self.rest.show_rest(self.context)
        self.assertEqual(self.data.pose_position, 'POSE')

    def test_resume_success_clears_marker_and_failure_restores_rest(self):
        self.rest.show_rest(self.context)
        marker = self.rig[self.rest.PREVIEW_KEY]
        with self.assertRaisesRegex(ValueError, 'rejected operation'):
            with self.rest.resume_for_operation(self.context, self.rig):
                self.assertEqual(self.data.pose_position, 'POSE')
                raise ValueError('rejected operation')
        self.assertEqual(self.data.pose_position, 'REST')
        self.assertEqual(self.rig[self.rest.PREVIEW_KEY], marker)
        with self.rest.resume_for_operation(self.context, self.rig):
            self.assertEqual(self.data.pose_position, 'POSE')
        self.assertEqual(self.data.pose_position, 'POSE')
        self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)
        self.assert_artist_untouched()

    def test_success_has_no_native_update_or_validation_after_backend_commit(self):
        self.rest.show_rest(self.context)
        original_update = self.context.view_layer.update
        with self.rest.resume_for_operation(self.context, self.rig):
            # Any post-commit native update/target revalidation would fail.
            original_update.side_effect = RuntimeError('post-commit native update')
            self.saved.rig = NS(type='ARMATURE')
        self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)
        self.assertEqual(self.data.pose_position, 'POSE')

    def test_successful_backend_data_swap_preserves_returned_artist_display(self):
        self.rest.show_rest(self.context)
        retained = NS(name_full='Retained original', pose_position='REST')
        with self.rest.resume_for_operation(self.context, self.rig):
            self.rig.data = retained
        self.assertIs(self.rig.data, retained)
        self.assertEqual(retained.pose_position, 'REST')
        self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)

    def test_unowned_native_rest_is_not_automatically_changed(self):
        self.data.pose_position = 'REST'
        with self.rest.resume_for_operation(self.context, self.rig):
            self.assertEqual(self.data.pose_position, 'REST')
        self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)

    def test_renamed_or_replaced_data_requires_explicit_pose(self):
        self.rest.show_rest(self.context)
        self.data.name_full = 'Renamed by artist'
        with self.assertRaisesRegex(ValueError, 'data changed'):
            with self.rest.resume_for_operation(self.context, self.rig):
                self.fail('A stale display marker was accepted.')
        self.assertEqual(self.data.pose_position, 'REST')
        self.rest.show_pose(self.context)
        self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)

    def test_display_update_failure_restores_previous_display(self):
        self.context.view_layer.update.side_effect = [RuntimeError('display update'), None]
        with self.assertRaisesRegex(RuntimeError, 'display update'):
            self.rest.show_rest(self.context)
        self.assertEqual(self.data.pose_position, 'POSE')
        self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)
        self.assert_artist_untouched()

    def actual_worklist_function(self, name, extra):
        path = SOURCE / 'animation_worklist.py'
        tree = ast.parse(path.read_bytes())
        private = {'activate': '_activate_worklist_action', 'adopt_main_rig': '_adopt_main_rig',
                   'restore_main_rig': '_restore_main_rig'}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {name, private[name]}]
        namespace = {'__package__': self.name, 'state': lambda context: self.saved,
                     '_rigging_module': lambda *args: None, **extra}
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                     str(path), 'exec'), namespace)
        return namespace[name]

    def service(self, name, **fields):
        module = ModuleType(self.name + '.' + name)
        module.__dict__.update(fields)
        sys.modules[module.__name__] = module
        setattr(self.package, name, module)
        return module

    def test_actual_adopt_and_restore_resume_before_backend_and_rollback_rejection(self):
        self.service('animation_composition', active=lambda rig: False)
        for function in ('adopt_main_rig', 'restore_main_rig'):
            with self.subTest(function=function):
                def accepted(context):
                    self.assertEqual(self.data.pose_position, 'POSE')
                    return self.rig
                target = self.service('animation_worklist_target', **{function: accepted})
                operation = self.actual_worklist_function(function, {})
                self.rest.show_rest(self.context)
                self.assertIs(operation(self.context), self.rig)
                self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)
                self.rest.show_rest(self.context)
                def rejected(context):
                    self.assertEqual(self.data.pose_position, 'POSE')
                    raise ValueError('backend rejected')
                setattr(target, function, rejected)
                with self.assertRaisesRegex(ValueError, 'backend rejected'):
                    operation(self.context)
                self.assertTrue(self.rest.is_rest(self.rig))

    def test_actual_activate_guard_failure_returns_to_rest(self):
        self.service('animation_composition', active=lambda rig: False)
        self.service('animation_worklist_target')
        def rejected(*args, **kwargs):
            self.assertEqual(self.data.pose_position, 'POSE')
            raise ValueError('busy or mesh edit')
        activate = self.actual_worklist_function('activate', {'_idle': rejected})
        self.rest.show_rest(self.context)
        with self.assertRaisesRegex(ValueError, 'busy or mesh edit'):
            activate(self.context, 'unused')
        self.assertTrue(self.rest.is_rest(self.rig))
        self.assert_artist_untouched()

    def test_actual_custom_activation_resumes_pose_and_retains_keys(self):
        self.service('animation_composition', active=lambda rig: False)
        def validated(context, rig):
            self.assertEqual(self.data.pose_position, 'POSE')
        self.service('animation_worklist_target', ensure_active=validated)
        self.context.object = self.rig
        self.rig.animation_data_create = lambda: self.rig.animation_data
        self.rig.select_set = Mock()
        self.rig.hide_set = Mock()
        self.context.selected_objects = []
        self.context.window_manager = NS(character_designer_animation=NS(target=None))
        self.context.scene.tool_settings = NS(use_keyframe_insert_auto=True)
        self.action.library, self.action.frame_range = None, (1.0, 24.0)
        item = NS(item_id='work', rig=self.rig, name='Walk', side='SOURCE',
                  new_animation=True, source_action=None, custom_action=self.action,
                  source_slot=0, custom_slot=17)
        self.saved.items, self.saved.active_index = [item], 0
        self.ua.active_preview = lambda rig: None
        self.ua._set_frame = Mock()
        activate = self.actual_worklist_function('activate', {
            '_idle': lambda *args, **kwargs: None,
            '_item': lambda saved, item_id: item,
            '_item_workspace': lambda item: False,
            '_check_baseline': lambda item: None,
            '_slot': lambda action, handle: NS(handle=handle),
            '_finish_rig': lambda *args: None,
            'ua': self.ua,
            'math': __import__('math'),
        })
        self.rest.show_rest(self.context)
        self.assertIs(activate(self.context, item.item_id), self.action)
        self.assertEqual(self.data.pose_position, 'POSE')
        self.assertNotIn(self.rest.PREVIEW_KEY, self.rig)
        self.assertIs(self.rig.animation_data.action, self.action)
        self.assertEqual(self.action['keys'], [('location', 1, 2.0)])
        self.assertEqual(item.side, 'CUSTOM')


if __name__ == '__main__':
    unittest.main()
