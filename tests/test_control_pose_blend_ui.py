"""Pure interaction spies for Pose Blend's adapters, menus and modal input.

These checks do not claim native matching, artist preservation, GUI Undo or FPS.
The production service's native tests cover evaluated transforms separately.
"""

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class Layout:
    def __init__(self):
        self.events = []
        self.operator_context = 'EXEC_DEFAULT'

    def operator(self, identifier, *args, **kwargs):
        result = NS()
        self.events.append(('operator', identifier, result, args, kwargs))
        return result

    def separator(self):
        self.events.append(('separator',))

    def menu(self, name):
        self.events.append(('menu', name))


class PoseBlendUI(unittest.TestCase):
    def setUp(self):
        self.package_name = '_cd_blend_ui_spy'
        self.module_name = self.package_name + '.control_pose_blend_ui'
        package = types.ModuleType(self.package_name)
        package.__path__ = [str(SOURCE)]
        bpy = types.ModuleType('bpy')
        props = types.ModuleType('bpy.props')
        for name in ('BoolProperty', 'EnumProperty', 'FloatProperty', 'StringProperty'):
            setattr(props, name, lambda **kwargs: kwargs)

        class Operator:
            is_registered = False

            def report(self, kinds, message):
                self.messages = getattr(self, 'messages', []) + [(kinds, message)]

        class Menu:
            def draw(self, context):
                pass

            draw._draw_funcs = []

            @classmethod
            def append(cls, callback):
                cls.draw._draw_funcs.append(callback)

            @classmethod
            def remove(cls, callback):
                cls.draw._draw_funcs.remove(callback)

        bpy.types = NS(Operator=Operator, ASSETBROWSER_MT_context_menu=Menu)
        bpy.props = props
        bpy.data = NS(actions={})
        bpy.ops = NS(poselib=NS(blend_pose_asset=Mock(return_value={'FINISHED'})))
        bpy.utils = NS(register_class=lambda cls: setattr(cls, 'is_registered', True),
                       unregister_class=lambda cls: setattr(cls, 'is_registered', False))
        self.body = types.ModuleType(self.package_name + '.body_setup')
        self.body.has_generated = Mock(return_value=True)
        self.modules = patch.dict(sys.modules, {'bpy': bpy, 'bpy.props': props,
            self.package_name: package, self.package_name + '.body_setup': self.body})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location(self.module_name,
                                                    SOURCE / 'control_pose_blend_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        sys.modules[self.module_name] = self.ui
        self.addCleanup(lambda: sys.modules.pop(self.module_name, None))
        spec.loader.exec_module(self.ui)
        self.poses = NS(ASSET_METADATA='test_pose_metadata', _asset_channels=Mock(return_value={}))
        poses_patch = patch.object(self.ui, '_poses', return_value=self.poses)
        poses_patch.start()
        self.addCleanup(poses_patch.stop)
        self.bpy = bpy
        self.rig = NS(name='Character', type='ARMATURE', mode='POSE')
        self.asset = NS(name='Pose', id_type='ACTION', local_id=None,
                        full_library_path='D:/Poses/Library.blend')
        self.context = NS(object=self.rig, asset=self.asset, area=NS(header_text_set=Mock()),
            workspace=NS(status_text_set=Mock()), window=NS(cursor_modal_set=Mock(),
            cursor_modal_restore=Mock()), window_manager=NS(modal_handler_add=Mock(),
            invoke_props_dialog=Mock(return_value={'RUNNING_MODAL'})))

        class NativeBlendRequired(ValueError):
            pass

        self.core = NS(NativeBlendRequired=NativeBlendRequired, begin=Mock(return_value=object()),
                       preview=Mock(), confirm=Mock(), cancel=Mock())
        self.core_patch = patch.object(self.ui, '_core', return_value=self.core)
        self.core_patch.start()
        self.addCleanup(self.core_patch.stop)

    def operator(self, modal=False):
        op = self.ui.CHARACTERDESIGNER_OT_blend_control_pose()
        for name in ('asset_name', 'asset_local_name', 'asset_library_path', 'asset_digest', 'target_name'):
            setattr(op, name, '')
        op.blend_factor, op.flipped = 0., False
        if modal:
            op._session = object()
            op._area, op._window, op._workspace = self.context.area, self.context.window, self.context.workspace
            op._mouse_x, op._mouse_factor = 100, 0.
            op._numeric, op._last_preview, op._applied_factor = '', 0., 0.
            op._values, op._metadata = {'hand.L': {}}, None
            self.ui._ACTIVE_OPERATORS.append(op)
        return op

    @staticmethod
    def event(kind, char='', value='PRESS', mouse_x=100, shift=False):
        return NS(type=kind, unicode=char, value=value, mouse_x=mouse_x, shift=shift)

    def gui(self):
        def draw(self, context):
            self.layout.separator()
            self.layout.operator('poselib.apply_pose_asset', text='Apply').flipped = False
            self.layout.operator('poselib.apply_pose_asset', text='Flipped').flipped = True
            self.layout.operator('poselib.blend_pose_asset', text='Blend').flipped = False
            self.layout.operator('poselib.blend_pose_asset', text='Blend Flipped').flipped = True
            self.layout.operator('poselib.pose_asset_select_bones').select = True
            self.layout.menu('VIEW3D_MT_pose_modify')
            self.layout.operator('asset.assign_action')

        class Shelf:
            bl_activate_operator = 'POSELIB_OT_apply_pose_asset'
            bl_drag_operator = 'POSELIB_OT_blend_pose_asset'

            @classmethod
            def draw_context_menu(cls, context, asset, layout):
                draw(NS(layout=layout), context)

        gui = NS(pose_library_asset_browser_context_menu=draw, VIEW3D_AST_pose_library=Shelf)
        before, after = Mock(name='foreign_before'), Mock(name='foreign_after')
        self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs = [before, draw, after]
        gui_patch = patch.dict(sys.modules, {'pose_library.gui': gui})
        gui_patch.start()
        self.addCleanup(gui_patch.stop)
        return gui, before, after

    @staticmethod
    def identifiers(layout):
        return [event[1] for event in layout.events if event[0] == 'operator']

    def test_controlled_and_plain_native_routing_policy(self):
        self.assertTrue(self.ui._controlled(self.context))
        self.body.has_generated.return_value = False
        self.assertFalse(self.ui._controlled(self.context))
        self.asset.local_id = {self.ui._poses().ASSET_METADATA: '{}'}
        self.assertTrue(self.ui._controlled(self.context))

    def test_menu_routes_exact_apply_blend_and_retains_mirror_properties(self):
        gui, before, after = self.gui()
        original = tuple(self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs)
        descriptor = gui.VIEW3D_AST_pose_library.__dict__['draw_context_menu']
        self.assertTrue(self.ui.route_native_menu())
        callbacks = self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs
        self.assertIs(callbacks[0], before)
        self.assertIs(callbacks[2], after)
        layout = Layout()
        callbacks[1](NS(layout=layout), self.context)
        self.assertEqual(self.identifiers(layout), [
            'character_designer.apply_control_pose', 'character_designer.apply_control_pose',
            'character_designer.blend_control_pose', 'character_designer.blend_control_pose',
            'poselib.pose_asset_select_bones', 'asset.assign_action'])
        operators = [event for event in layout.events if event[0] == 'operator']
        self.assertEqual([event[2].flipped for event in operators[:4]], [False, True, False, True])
        self.assertIn(('menu', 'VIEW3D_MT_pose_modify'), layout.events)
        self.ui._release_hooks()
        self.assertEqual(tuple(callbacks), original)
        self.assertIs(gui.VIEW3D_AST_pose_library.__dict__['draw_context_menu'], descriptor)

    def test_plain_native_menu_keeps_original_identifiers(self):
        self.gui()
        self.body.has_generated.return_value = False
        self.ui.route_native_menu()
        layout = Layout()
        self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs[1](NS(layout=layout), self.context)
        self.assertEqual(self.identifiers(layout)[:4], ['poselib.apply_pose_asset'] * 2 +
                         ['poselib.blend_pose_asset'] * 2)

    def test_menu_uses_real_draw_function_callbacks_and_ignores_class_decoy(self):
        gui, before, after = self.gui()
        menu = self.bpy.types.ASSETBROWSER_MT_context_menu
        decoy = [Mock(name='class_level_decoy')]
        menu._draw_funcs = decoy
        actual = menu.draw._draw_funcs
        self.assertTrue(self.ui.route_native_menu())
        self.assertIs(menu._draw_funcs, decoy)
        self.assertEqual(len(decoy), 1)
        self.assertIs(menu.draw._draw_funcs, actual)
        self.assertIs(actual[0], before)
        self.assertIs(actual[2], after)
        self.assertIsNot(actual[1], gui.pose_library_asset_browser_context_menu)
        self.ui._release_hooks()
        self.assertEqual(actual, [before, gui.pose_library_asset_browser_context_menu, after])
        self.assertIs(menu._draw_funcs, decoy)

    def test_class_level_original_callback_cannot_fake_a_real_menu_slot(self):
        gui, _, _ = self.gui()
        menu = self.bpy.types.ASSETBROWSER_MT_context_menu
        menu.draw._draw_funcs = []
        decoy = [gui.pose_library_asset_browser_context_menu]
        menu._draw_funcs = decoy
        self.assertFalse(self.ui.route_native_menu())
        self.assertIsNone(self.ui._MENU_HOOK)
        self.assertEqual(menu.draw._draw_funcs, [])
        self.assertIs(menu._draw_funcs, decoy)

    def test_shelf_preserves_other_items_and_adds_offset_once(self):
        gui, _, _ = self.gui()
        native_activation = gui.VIEW3D_AST_pose_library.bl_activate_operator
        native_drag = gui.VIEW3D_AST_pose_library.bl_drag_operator
        self.ui.route_native_menu()
        hook = gui.VIEW3D_AST_pose_library.__dict__['draw_context_menu']
        self.ui.route_native_menu()
        self.assertIs(gui.VIEW3D_AST_pose_library.__dict__['draw_context_menu'], hook)
        layout = Layout()
        gui.VIEW3D_AST_pose_library.draw_context_menu(self.context, self.asset, layout)
        self.assertEqual(self.identifiers(layout)[-1], 'character_designer.offset_animation_pose')
        self.assertEqual(self.identifiers(layout).count('character_designer.offset_animation_pose'), 1)
        self.assertIn('asset.assign_action', self.identifiers(layout))
        # Thumbnail activation/drag remain explicitly native in this release.
        # A Python class-attribute assignment would not prove native ShelfType
        # registration changed, so the adapters must not pretend to replace it.
        self.assertEqual(gui.VIEW3D_AST_pose_library.bl_activate_operator, native_activation)
        self.assertEqual(gui.VIEW3D_AST_pose_library.bl_drag_operator, native_drag)

    def test_unregister_preserves_foreign_replacement_and_callback_order(self):
        gui, before, after = self.gui()
        self.ui.register()
        foreign = classmethod(lambda cls, context, asset, layout: None)
        gui.VIEW3D_AST_pose_library.draw_context_menu = foreign
        extra = Mock(name='late_foreign')
        self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs.append(extra)
        self.ui.unregister()
        self.assertEqual(self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs,
                         [before, gui.pose_library_asset_browser_context_menu, after, extra])
        self.assertIs(gui.VIEW3D_AST_pose_library.__dict__['draw_context_menu'], foreign)
        self.assertTrue(all(not cls.is_registered for cls in self.ui.CLASSES))

    def test_registration_failure_after_hook_installation_rolls_back_owned_state(self):
        gui, before, after = self.gui()
        original_shelf = gui.VIEW3D_AST_pose_library.__dict__['draw_context_menu']
        route = self.ui.route_native_menu
        def fail():
            route()
            raise RuntimeError('injected post-hook failure')
        with patch.object(self.ui, 'route_native_menu', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'post-hook'):
                self.ui.register()
        self.assertEqual(self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs,
                         [before, gui.pose_library_asset_browser_context_menu, after])
        self.assertIs(gui.VIEW3D_AST_pose_library.__dict__['draw_context_menu'], original_shelf)
        self.assertTrue(all(not cls.is_registered for cls in self.ui.CLASSES))
        self.assertIsNone(self.ui._MENU_HOOK)
        self.assertIsNone(self.ui._SHELF_HOOK)

    def test_failed_repeat_registration_keeps_preexisting_owned_hooks(self):
        self.gui()
        self.ui.register()
        menu, shelf = self.ui._MENU_HOOK, self.ui._SHELF_HOOK
        with patch.object(self.ui, 'route_native_menu', side_effect=RuntimeError('later failure')):
            with self.assertRaises(RuntimeError):
                self.ui.register()
        self.assertIs(self.ui._MENU_HOOK, menu)
        self.assertIs(self.ui._SHELF_HOOK, shelf)
        self.assertTrue(all(cls.is_registered for cls in self.ui.CLASSES))

    def test_active_modal_blocks_unregister_before_any_hook_or_class_mutation(self):
        self.gui()
        self.ui.register()
        menu, shelf = self.ui._MENU_HOOK, self.ui._SHELF_HOOK
        self.operator(modal=True)
        with self.assertRaisesRegex(RuntimeError, 'Finish or cancel'):
            self.ui.unregister()
        self.assertIs(self.ui._MENU_HOOK, menu)
        self.assertIs(self.ui._SHELF_HOOK, shelf)
        self.assertTrue(all(cls.is_registered for cls in self.ui.CLASSES))

    def test_numeric_decimal_backspace_and_enter_commit_once(self):
        op = self.operator(modal=True)
        for char in '0.5':
            self.assertEqual(op.modal(self.context, self.event('DIGIT', char)), {'RUNNING_MODAL'})
        self.assertEqual(op.blend_factor, .5)
        self.assertEqual(op.modal(self.context, self.event('BACK_SPACE')), {'RUNNING_MODAL'})
        self.assertEqual(op.blend_factor, 0.)
        op.modal(self.context, self.event('DIGIT', '2'))
        self.assertEqual(op.blend_factor, .2)
        self.core.confirm.assert_not_called()
        self.assertEqual(op.modal(self.context, self.event('RET')), {'FINISHED'})
        self.core.confirm.assert_called_once()
        self.assertEqual(self.core.preview.call_args.args[-1], .2)
        self.core.cancel.assert_not_called()
        self.assertIsNone(op._session)
        self.assertNotIn(op, self.ui._ACTIVE_OPERATORS)
        self.context.window.cursor_modal_restore.assert_called_once()

    def test_numeric_is_bounded_and_duplicate_decimal_does_not_corrupt_input(self):
        op = self.operator(modal=True)
        op.modal(self.context, self.event('DIGIT', '8'))
        self.assertEqual(op.blend_factor, 1.)
        for _ in range(3):
            op.modal(self.context, self.event('BACK_SPACE'))
        self.assertEqual(op.blend_factor, 0.)
        for char in '.5.':
            op.modal(self.context, self.event('DIGIT', char))
        self.assertEqual((op._numeric, op.blend_factor), ('.5', .5))
        op.modal(self.context, self.event('LEFT_ARROW', shift=True))
        self.assertAlmostEqual(op.blend_factor, .499)
        self.assertEqual(op._numeric, '')

    def test_mouse_preview_is_throttled_but_confirm_applies_latest_factor(self):
        op = self.operator(modal=True)
        with patch.object(self.ui.time, 'monotonic', side_effect=[1., 1.01]):
            op.modal(self.context, self.event('MOUSEMOVE', mouse_x=160))
            op.modal(self.context, self.event('MOUSEMOVE', mouse_x=220))
        self.assertEqual(self.core.preview.call_count, 1)
        self.assertEqual(op.blend_factor, .4)
        self.assertEqual(op._applied_factor, .2)
        op.modal(self.context, self.event('LEFTMOUSE'))
        self.assertEqual(self.core.preview.call_args.args[-1], .4)
        self.core.confirm.assert_called_once()

    def test_escape_and_right_mouse_cancel_without_confirm(self):
        for key in ('ESC', 'RIGHTMOUSE'):
            with self.subTest(key=key):
                op = self.operator(modal=True)
                self.core.cancel.reset_mock()
                self.assertEqual(op.modal(self.context, self.event(key)), {'CANCELLED'})
                self.core.cancel.assert_called_once()
                self.core.preview.assert_not_called()
                self.core.confirm.assert_not_called()
                self.assertIsNone(op._session)
                self.assertNotIn(op, self.ui._ACTIVE_OPERATORS)

    def test_mirror_toggle_restarts_from_cancelled_start_not_previous_preview(self):
        op = self.operator(modal=True)
        old = op._session
        op.blend_factor = .5
        self.assertEqual(op.modal(self.context, self.event('LEFT_CTRL')), {'RUNNING_MODAL'})
        self.core.cancel.assert_called_once_with(self.context, old)
        self.assertTrue(op.flipped)
        self.core.begin.assert_called_once_with(self.context, rig=self.rig,
            values=op._values, metadata=None, flipped=True)
        self.assertEqual(self.core.preview.call_args.args[-1], .5)
        self.core.confirm.assert_not_called()

    def test_preview_error_cancels_owned_session_and_restores_modal_ui(self):
        op = self.operator(modal=True)
        self.core.preview.side_effect = ValueError('changed outside owner')
        self.assertEqual(op.modal(self.context, self.event('DIGIT', '5')), {'CANCELLED'})
        self.core.cancel.assert_called_once()
        self.core.confirm.assert_not_called()
        self.assertIn('changed outside owner', op.messages[-1][1])
        self.assertIsNone(op._session)
        self.assertNotIn(op, self.ui._ACTIVE_OPERATORS)

    def test_blender_cancel_callback_restores_session_and_ui_even_if_recovery_refused(self):
        op = self.operator(modal=True)
        self.core.cancel.side_effect = ValueError('foreign edit kept')
        with self.assertRaisesRegex(ValueError, 'foreign edit'):
            op.cancel(self.context)
        self.assertIsNone(op._session)
        self.assertNotIn(op, self.ui._ACTIVE_OPERATORS)
        self.context.window.cursor_modal_restore.assert_called_once()

    def test_closed_area_does_not_skip_status_or_cursor_cleanup(self):
        op = self.operator(modal=True)
        op._area.header_text_set.side_effect = ReferenceError('area was closed')
        self.ui._end_ui(op)
        self.context.workspace.status_text_set.assert_called_once_with(None)
        self.context.window.cursor_modal_restore.assert_called_once()
        self.assertNotIn(op, self.ui._ACTIVE_OPERATORS)

    def test_invoke_preview_then_modal_registration_without_confirm_or_timer(self):
        op = self.operator()
        op._values, op._metadata = {'hand.L': {}}, None
        with patch.object(self.ui, '_remember'):
            self.assertEqual(op.invoke(self.context, self.event('MOUSEMOVE')), {'RUNNING_MODAL'})
        self.core.preview.assert_called_once_with(self.context, op._session, 0.)
        self.core.confirm.assert_not_called()
        self.context.window_manager.modal_handler_add.assert_called_once_with(op)
        self.assertIn(op, self.ui._ACTIVE_OPERATORS)

    def test_execute_commits_service_once_without_ui_registration(self):
        op = self.operator()
        op.asset_name = 'Pose'
        op._values, op._metadata = {'hand.L': {}}, None
        op.blend_factor = .75
        with patch.object(self.ui, '_load'):
            self.assertEqual(op.execute(self.context), {'FINISHED'})
        self.core.preview.assert_called_once()
        self.core.confirm.assert_called_once()
        self.context.window_manager.modal_handler_add.assert_not_called()
        self.assertEqual(self.ui._ACTIVE_OPERATORS, [])

    def test_remember_local_asset_captures_local_identity_and_digest(self):
        op = self.operator()
        local = NS(name='Local Pose Datablock', library=None, asset_data=True)
        self.asset.local_id = local
        self.bpy.data.actions[local.name] = local
        self.poses._asset_channels.return_value = {'hand.L': {'location': {0: .2}}}
        self.ui._remember(op, self.context)
        self.assertEqual((op.asset_name, op.asset_local_name, op.asset_library_path),
                         ('Pose', local.name, ''))
        self.assertEqual(op.target_name, self.rig.name)
        self.assertEqual(len(op.asset_digest), 64)

    def test_changed_asset_digest_refuses_redo_before_service_or_native_calls(self):
        op = self.operator()
        self.poses._asset_channels.return_value = {'hand.L': {'location': {0: .2}}}
        self.ui._remember(op, self.context)
        self.poses._asset_channels.return_value = {'hand.L': {'location': {0: .3}}}
        self.assertEqual(op.execute(self.context), {'CANCELLED'})
        self.assertIn('Pose asset changed', op.messages[-1][1])
        self.core.begin.assert_not_called()
        self.core.preview.assert_not_called()
        self.bpy.ops.poselib.blend_pose_asset.assert_not_called()

    def test_changed_character_refuses_before_loading_asset(self):
        op = self.operator()
        self.ui._remember(op, self.context)
        self.poses._asset_channels.reset_mock()
        self.context.object = NS(name='Other Character', type='ARMATURE', mode='POSE')
        self.assertEqual(op.execute(self.context), {'CANCELLED'})
        self.poses._asset_channels.assert_not_called()
        self.core.begin.assert_not_called()

    def test_native_asset_fallback_preserves_factor_and_mirror_without_managed_preview(self):
        op = self.operator()
        op.blend_factor, op.flipped = .4, True
        self.poses._asset_channels.return_value = None
        self.assertEqual(op.execute(self.context), {'FINISHED'})
        self.bpy.ops.poselib.blend_pose_asset.assert_called_once_with('EXEC_DEFAULT',
            blend_factor=.4, flipped=True)
        self.core.preview.assert_not_called()
        self.core.confirm.assert_not_called()
        self.assertEqual(self.ui._ACTIVE_OPERATORS, [])

    def test_native_fallback_rejects_same_name_from_different_library(self):
        op = self.operator()
        self.ui._remember(op, self.context)
        self.context.asset = NS(name=self.asset.name, id_type='ACTION', local_id=None,
                                full_library_path='D:/Other/Library.blend')
        with self.assertRaises(ValueError):
            self.ui._native(op, self.context, False)
        self.bpy.ops.poselib.blend_pose_asset.assert_not_called()

    def test_native_fallback_rejects_same_display_name_from_different_local_action(self):
        op = self.operator()
        original = NS(name='Original Local Pose', library=None, asset_data=True)
        self.asset.local_id = original
        self.bpy.data.actions[original.name] = original
        self.ui._remember(op, self.context)
        self.context.asset = NS(name=self.asset.name, id_type='ACTION',
            local_id=NS(name='Other Local Pose', library=None, asset_data=True),
            full_library_path='')
        with self.assertRaises(ValueError):
            self.ui._native(op, self.context, False)
        self.bpy.ops.poselib.blend_pose_asset.assert_not_called()

    def test_offset_dialog_passes_explicit_scope_mirror_and_new_action_activation(self):
        op = self.ui.CHARACTERDESIGNER_OT_offset_animation_pose()
        op.asset_name, op.strength, op.scope, op.flipped = 'Pose', .5, 'ARMS', True
        offset = types.ModuleType(self.package_name + '.control_pose_offset')
        offset.create = Mock(return_value={'name': 'Pose Offset'})
        with patch.dict(sys.modules, {offset.__name__: offset}), \
                patch.object(self.ui, '_load', return_value=(self.asset, {}, None)), \
                patch.object(self.ui, '_offset_names', return_value=['upper_arm.L', 'forearm.L']):
            self.assertEqual(op.execute(self.context), {'FINISHED'})
        offset.create.assert_called_once_with(self.context, self.rig, self.asset, strength=.5,
            bone_names=['upper_arm.L', 'forearm.L'], flipped=True, activate=True)

    def outer_registration(self):
        """Compile the real outer lifecycle only, without mathutils/bpy imports."""
        source = SOURCE / 'control_pose_assets.py'
        wanted = {'_menu', '_route_double_click', 'register', 'unregister'}
        tree = ast.parse(source.read_bytes())
        subset = ast.Module(body=[node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted], type_ignores=[])
        namespace = {'__name__': self.package_name + '.outer_registration_spy',
                     '__package__': self.package_name, 'bpy': self.bpy,
                     '_KEYMAPS': [], '_REPLACED': []}

        class KeyItems(list):
            def new(items, identifier, kind, value, **kwargs):
                item = NS(idname=identifier, type=kind, value=value, properties=NS(), **kwargs)
                items.append(item)
                return item

        keymap = NS(name='Asset Browser Main', keymap_items=KeyItems())
        config = NS(keymaps=NS(get=lambda name: keymap, new=lambda **kwargs: keymap))
        self.bpy.context = NS(window_manager=NS(keyconfigs=NS(addon=config)))
        timer_set = set()
        self.bpy.app = NS(background=False, timers=NS(
            is_registered=lambda fn: fn in timer_set,
            register=lambda fn, **kwargs: timer_set.add(fn),
            unregister=lambda fn: timer_set.remove(fn)))
        namespace['CHARACTERDESIGNER_OT_apply_control_pose'] = type('ApplyPose', (), {
            'is_registered': False, 'bl_idname': 'character_designer.apply_control_pose'})
        native = NS(idname='poselib.apply_pose_asset', active=True, type='LEFTMOUSE',
                    value='DOUBLE_CLICK', ctrl=False, alt=False, shift=False, oskey=False, any=False)
        foreign = NS(idname='poselib.apply_pose_asset', active=True, type='LEFTMOUSE',
                     value='DOUBLE_CLICK', ctrl=True, alt=False, shift=False, oskey=False, any=False)
        keys = patch.dict(sys.modules, {'pose_library.keymaps': NS(addon_keymaps=[
            (keymap, native), (keymap, foreign)])})
        keys.start()
        self.addCleanup(keys.stop)
        exec(compile(subset, str(source), 'exec'), namespace)
        return namespace, keymap, native, foreign, timer_set

    def test_outer_registration_failure_after_binding_changes_restores_owned_suffix(self):
        gui, before, after = self.gui()
        outer, keymap, native, foreign, timers = self.outer_registration()
        route = outer['_route_double_click']
        def fail():
            route()
            raise RuntimeError('outer route failure')
        outer['_route_double_click'] = fail
        with self.assertRaisesRegex(RuntimeError, 'outer route failure'):
            outer['register']()
        self.assertEqual(outer['_KEYMAPS'], [])
        self.assertEqual(keymap.keymap_items, [])
        self.assertEqual(outer['_REPLACED'], [])
        self.assertTrue(native.active)
        self.assertTrue(foreign.active)
        self.assertEqual(timers, set())
        self.assertFalse(outer['CHARACTERDESIGNER_OT_apply_control_pose'].is_registered)
        self.assertTrue(all(not cls.is_registered for cls in self.ui.CLASSES))
        self.assertEqual(self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs,
                         [before, gui.pose_library_asset_browser_context_menu, after])

    def test_outer_registration_timer_postinstall_failure_removes_only_new_timer(self):
        self.gui()
        outer, keymap, native, foreign, timers = self.outer_registration()
        def fail(fn, **kwargs):
            timers.add(fn)
            raise RuntimeError('timer postinstall failure')
        self.bpy.app.timers.register = fail
        with self.assertRaisesRegex(RuntimeError, 'timer postinstall failure'):
            outer['register']()
        self.assertEqual(timers, set())
        self.assertEqual(keymap.keymap_items, [])
        self.assertTrue(native.active and foreign.active)
        self.assertTrue(all(not cls.is_registered for cls in self.ui.CLASSES))

    def test_outer_failure_keeps_preexisting_owned_prefix_and_foreign_binding(self):
        self.gui()
        outer, keymap, native, foreign, timers = self.outer_registration()
        outer['register']()
        old_keys, old_replaced = tuple(outer['_KEYMAPS']), tuple(outer['_REPLACED'])
        old_menu, old_shelf = self.ui._MENU_HOOK, self.ui._SHELF_HOOK
        old_timers = set(timers)
        with patch.object(self.ui, 'route_native_menu', side_effect=RuntimeError('repeat route failure')):
            with self.assertRaisesRegex(RuntimeError, 'repeat route failure'):
                outer['register']()
        self.assertEqual(tuple(outer['_KEYMAPS']), old_keys)
        self.assertEqual(tuple(outer['_REPLACED']), old_replaced)
        self.assertIs(self.ui._MENU_HOOK, old_menu)
        self.assertIs(self.ui._SHELF_HOOK, old_shelf)
        self.assertEqual(timers, old_timers)
        self.assertFalse(native.active)
        self.assertTrue(foreign.active)
        self.assertTrue(all(cls.is_registered for cls in self.ui.CLASSES))

    def test_outer_failure_with_partially_registered_ui_restores_exact_prefix(self):
        gui, before, after = self.gui()
        outer, keymap, native, foreign, timers = self.outer_registration()
        self.ui.CLASSES[0].is_registered = True
        route = outer['_route_double_click']
        def fail():
            route()
            raise RuntimeError('partial prefix failure')
        outer['_route_double_click'] = fail
        with self.assertRaisesRegex(RuntimeError, 'partial prefix failure'):
            outer['register']()
        self.assertTrue(self.ui.CLASSES[0].is_registered)
        self.assertFalse(self.ui.CLASSES[1].is_registered)
        self.assertEqual(self.bpy.types.ASSETBROWSER_MT_context_menu.draw._draw_funcs,
                         [before, gui.pose_library_asset_browser_context_menu, after])
        self.assertIsNone(self.ui._MENU_HOOK)
        self.assertIsNone(self.ui._SHELF_HOOK)
        self.assertEqual(keymap.keymap_items, [])
        self.assertTrue(native.active and foreign.active)


if __name__ == '__main__':
    unittest.main()
