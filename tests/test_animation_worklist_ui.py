"""Worklist drawing and stable-ID gestures without Blender or worker processes."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class Layout:
    def __init__(self, records=None, enabled=True):
        self.records = records if records is not None else []
        self.enabled = enabled
        self.operator_context = None

    def row(self, **_kwargs):
        return Layout(self.records, self.enabled)

    column = box = row

    def label(self, **kwargs):
        self.records.append(('label', kwargs, self.enabled))

    def prop(self, data, prop, **kwargs):
        getattr(data, prop)
        self.records.append(('prop', (prop, kwargs), self.enabled))

    def operator(self, name, **kwargs):
        payload = SimpleNamespace()
        self.records.append(('operator', (name, kwargs, payload), self.enabled))
        return payload

    def template_list(self, *args, **kwargs):
        self.records.append(('list', (args, kwargs), self.enabled))


class WorklistUI(unittest.TestCase):
    def setUp(self):
        package_name = '_worklist_ui_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        props = ModuleType('bpy.props')
        for name in ('BoolProperty', 'CollectionProperty', 'EnumProperty', 'IntProperty',
                     'PointerProperty', 'StringProperty'):
            setattr(props, name, lambda **kwargs: kwargs)
        types = ModuleType('bpy.types')

        class Operator:
            def report(self, level, message):
                self.last_report = (level, message)

        class UIList:
            bitflag_filter_item = 1 << 30

        types.Operator, types.UIList, types.PropertyGroup = Operator, UIList, object
        types.Action, types.Object, types.Armature = object, object, object
        bpy.props, bpy.types = props, types
        animation = ModuleType(package_name + '.animation')
        animation._link_idle = Mock(return_value=True)
        self.backend = ModuleType(package_name + '.animation_worklist')
        for method in ('connect', 'refresh', 'add', 'activate', 'remove', 'move', 'sync'):
            setattr(self.backend, method, Mock())
        modules = patch.dict(sys.modules, {
            package_name: package, 'bpy': bpy, 'bpy.props': props, 'bpy.types': types,
            animation.__name__: animation, self.backend.__name__: self.backend,
        })
        modules.start()
        self.addCleanup(modules.stop)
        package.animation_worklist = self.backend
        name = package_name + '.animation_worklist_ui'
        spec = importlib.util.spec_from_file_location(name, SOURCE / 'animation_worklist_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.ui
        spec.loader.exec_module(self.ui)
        self.addCleanup(self.ui.stop_worklist_ui)
        self.animation = animation
        rig = SimpleNamespace(animation_data=SimpleNamespace(action=None))
        items = [SimpleNamespace(item_id='id-' + name, clip_key='clip-' + name, name=name,
                                 source_action=object(), custom_action=object(), rig=rig, side='CUSTOM')
                 for name in ('Walk', 'Run', 'Idle')]
        rig.animation_data.action = items[0].custom_action
        catalog = [SimpleNamespace(clip_key='clip-' + name, name=name, source_name=source,
                                   link_path='link.json' if ready else '', ready=ready)
                   for name, source, ready in (('Walk', 'Soldier', True), ('Dance', 'Civilian', False))]
        self.state = SimpleNamespace(items=items, catalog=catalog, active_index=0, catalog_index=1,
                                     search='', workspace_path='character_animation_workspace.json',
                                     target_name='Cosha', show_catalog=True, status='', has_error=False)
        self.context = SimpleNamespace(
            scene=SimpleNamespace(character_designer_animation_worklist=self.state),
            screen=SimpleNamespace(areas=[]), region=SimpleNamespace(width=360),
            preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1.0)),
            workspace=SimpleNamespace(status_text_set=Mock()),
            window_manager=SimpleNamespace(modal_handler_add=Mock()))

    def load_animation(self):
        """Load the actual coordinator; only Blender/services are substitutes."""
        package_name = self.ui.__package__
        bpy = sys.modules['bpy']
        bpy.props.FloatProperty = lambda **kwargs: kwargs
        bpy.types.Panel = type('Panel', (), {})
        handlers = ModuleType('bpy.app.handlers')
        handlers.persistent = lambda function: function
        handlers.load_pre = []
        app = ModuleType('bpy.app')
        app.handlers = handlers
        app.timers = SimpleNamespace(is_registered=Mock(return_value=True), unregister=Mock())
        bpy.app = app
        sys.modules['bpy.app'], sys.modules['bpy.app.handlers'] = app, handlers
        runtime = ModuleType(package_name + '.animation_runtime')
        runtime.DEFAULT_RUNTIME_ROOT = 'Unused'
        runtime.LocalMotionJob, runtime.read_runtime = Mock(), Mock()
        exporter = ModuleType(package_name + '.animation_export')
        exporter.active_job, exporter.poll_export, exporter.cancel_export = Mock(), Mock(), Mock()
        model_exporter = ModuleType(package_name + '.unity_export')
        model_exporter.export_running = Mock(return_value=False)
        for module in (runtime, exporter, model_exporter):
            sys.modules[module.__name__] = module
            setattr(sys.modules[package_name], module.__name__.rsplit('.', 1)[1], module)
        name = package_name + '.animation'
        spec = importlib.util.spec_from_file_location(name, SOURCE / 'animation.py')
        animation = importlib.util.module_from_spec(spec)
        sys.modules[name] = animation
        spec.loader.exec_module(animation)
        animation._redraw = Mock()
        animation._export_window_manager = self.context.window_manager
        self.context.window_manager.character_designer_animation = SimpleNamespace(
            status='Syncing original scene', has_error=False, export_result_path='')
        self.state.status = 'Syncing original scene'
        self.origin_scene = self.context.scene
        self.other_state = SimpleNamespace(status='Other scene unchanged', has_error=False)
        self.context.scene = SimpleNamespace(character_designer_animation_worklist=self.other_state)
        self.job = {'_worklist_scene': self.origin_scene, '_worklist_item_id': 'id-Walk'}
        exporter.active_job.return_value = self.job
        exporter.poll_export.return_value = {'filepath': 'Edited Walk.fbx', 'link_manifest': 'character_animation_link.json'}
        self.exporter, self.coordinator = exporter, animation
        return animation

    def event(self, kind, y=800, value='NOTHING'):
        return SimpleNamespace(type=kind, mouse_y=y, value=value)

    def load_playback(self, *, connected=True):
        animation = self.load_animation()
        self.context.scene = self.origin_scene
        self.context.scene.objects = {}
        self.context.object = None
        self.context.window_manager.character_designer_animation.target = None
        self.state.workspace_path = 'character_animation_workspace.json' if connected else ''
        self.state.rig = None
        sys.modules['bpy'].data = SimpleNamespace(objects={})
        return animation

    @staticmethod
    def rig(name='CoshaRig.001', *, kind='ARMATURE', library=None):
        return SimpleNamespace(name=name, type=kind, library=library,
                               animation_data=None, get=Mock(return_value=False))

    def drag(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_drag()
        operator.item_id = 'id-Walk'
        self.assertEqual(operator.invoke(self.context, self.event('LEFTMOUSE')), {'RUNNING_MODAL'})
        return operator

    def test_rna_contract_has_no_action_switching_update_callbacks(self):
        state = self.ui.CharacterDesignerAnimationWorklistState.__annotations__
        self.assertNotIn('update', state['active_index'])
        self.assertNotIn('update', state['catalog_index'])
        self.assertNotIn('update', state['search'])
        for field in ('workspace_identity', 'workspace_id', 'target_guid', 'model_file', 'model_sha256', 'rig'):
            self.assertIn(field, state)
        item = self.ui.CharacterDesignerAnimationWorklistItem.__annotations__
        for field in ('item_id', 'source_action', 'custom_action', 'manifest_path', 'source_file', 'source_hash',
                      'link_identity', 'source_slot', 'custom_slot', 'source_data', 'custom_data'):
            self.assertIn(field, item)

    def test_draw_does_not_read_files_or_call_backend(self):
        layout = Layout()
        with patch.object(Path, 'open', side_effect=AssertionError('UI read a file')), \
                patch.object(self.ui, '_backend', side_effect=AssertionError('UI called the backend')):
            self.ui.draw_worklist(layout, self.context)
        labels = [value['text'] for kind, value, _enabled in layout.records if kind == 'label']
        self.assertIn('Character: Cosha', labels)
        self.assertIn('Source: Soldier', labels)
        self.assertIn('Prepare in Unity', labels)
        add = [(value, enabled) for kind, value, enabled in layout.records
               if kind == 'operator' and value[0] == 'character_designer.worklist_add']
        self.assertEqual(len(add), 1)
        self.assertFalse(add[0][1])
        self.assertEqual(add[0][0][2].clip_key, 'clip-Dance')

    def test_search_filters_name_and_source_without_reordering(self):
        ui_list = self.ui.CHARACTERDESIGNER_UL_animation_catalog()
        self.state.search = 'WALK soldier'
        flags, order = ui_list.filter_items(self.context, self.state, 'catalog')
        self.assertEqual(flags, [ui_list.bitflag_filter_item, 0])
        self.assertEqual(order, [])

    def test_search_hides_add_for_a_selected_row_outside_the_results(self):
        self.state.catalog_index = 0
        self.state.search = 'Dance'
        layout = Layout()
        self.ui.draw_worklist(layout, self.context)
        self.assertFalse(any(kind == 'operator' and value[0] == 'character_designer.worklist_add'
                             for kind, value, _enabled in layout.records))

    def test_row_actions_use_stable_item_ids_and_correct_sides(self):
        item = self.state.items[1]
        layout = Layout()
        self.ui.CHARACTERDESIGNER_UL_animation_worklist().draw_item(
            self.context, layout, self.state, item, 0, self.state, 'active_index', 1)
        buttons = [value for kind, value, _enabled in layout.records if kind == 'operator']
        for _name, _options, payload in buttons:
            self.assertEqual(payload.item_id, 'id-Run')
        sides = [payload.side for name, _options, payload in buttons if name.endswith('worklist_activate')]
        self.assertEqual(sides, ['SOURCE', 'CUSTOM'])
        activate = self.ui.CHARACTERDESIGNER_OT_worklist_activate()
        activate.item_id, activate.side = 'id-Run', 'SOURCE'
        self.state.items.reverse()
        self.assertEqual(activate.execute(self.context), {'FINISHED'})
        self.backend.activate.assert_called_once_with(self.context, 'id-Run', side='SOURCE')

    def test_row_exposes_only_source_custom_remove_and_legacy_move_is_hidden(self):
        layout = Layout()
        self.ui.CHARACTERDESIGNER_UL_animation_worklist().draw_item(
            self.context, layout, self.state, self.state.items[0], 0, self.state, 'active_index', 0)
        buttons = [value for kind, value, _enabled in layout.records if kind == 'operator']
        self.assertEqual([name for name, _options, _payload in buttons], [
            'character_designer.worklist_activate', 'character_designer.worklist_activate',
            'character_designer.worklist_remove'])
        self.assertEqual([payload.side for name, _options, payload in buttons
                          if name.endswith('worklist_activate')], ['SOURCE', 'CUSTOM'])
        self.assertIn('INTERNAL', self.ui.CHARACTERDESIGNER_OT_worklist_drag.bl_options)

    def test_sync_requires_selected_custom_to_be_the_actual_rig_action(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_sync
        self.assertTrue(operator.poll(self.context))
        self.state.active_index = 1
        self.assertFalse(operator.poll(self.context))
        self.state.active_index = 0
        item = self.state.items[0]
        item.side = 'SOURCE'
        item.rig.animation_data.action = item.source_action
        self.assertFalse(operator.poll(self.context))

    def test_drag_moves_once_on_release_with_stable_id_and_full_delta(self):
        operator = self.drag()
        self.assertEqual(operator.modal(self.context, self.event('MOUSEMOVE', y=750)), {'RUNNING_MODAL'})
        self.backend.move.assert_not_called()
        self.assertEqual([item.item_id for item in self.state.items], ['id-Walk', 'id-Run', 'id-Idle'])
        self.assertEqual(operator.modal(self.context, self.event('LEFTMOUSE', y=750, value='RELEASE')), {'FINISHED'})
        self.backend.move.assert_called_once_with(self.context, 'id-Walk', 2)
        self.assertFalse(self.ui._DRAGS)
        self.context.workspace.status_text_set.assert_called_with(None)

    def test_move_button_release_then_pointer_motion_requires_a_second_click(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_drag()
        operator.item_id = 'id-Walk'
        self.assertEqual(operator.invoke(self.context, self.event('LEFTMOUSE', value='RELEASE')),
                         {'RUNNING_MODAL'})
        self.backend.move.assert_not_called()
        operator.modal(self.context, self.event('MOUSEMOVE', y=750))
        self.assertIn('click to confirm', self.context.workspace.status_text_set.call_args.args[0])
        self.assertEqual(operator.modal(self.context, self.event('LEFTMOUSE', y=750, value='PRESS')),
                         {'RUNNING_MODAL'})
        self.backend.move.assert_not_called()
        self.assertEqual(operator.modal(self.context, self.event('LEFTMOUSE', y=750, value='RELEASE')),
                         {'FINISHED'})
        self.backend.move.assert_called_once_with(self.context, 'id-Walk', 2)

    def test_tall_drag_handle_uses_previous_press_position_once_on_release(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_drag_handle()
        operator.item_id = 'id-Walk'
        event = self.event('LEFTMOUSE', y=750, value='RELEASE')
        event.mouse_prev_press_y = 800
        event.mouse_prev_y = 751  # The latest motion sample must not replace the press origin.
        self.assertEqual(operator.invoke(self.context, event), {'FINISHED'})
        self.backend.move.assert_called_once_with(self.context, 'id-Walk', 2)
        self.context.window_manager.modal_handler_add.assert_not_called()
        self.assertFalse(self.ui._DRAGS)

    def test_tall_drag_handle_accounts_for_dpi_zoom_and_upward_direction(self):
        self.context.preferences.system.ui_scale = 1.5
        self.context.region.view2d = SimpleNamespace(region_to_view=lambda x, y: (x, y / 2.0))
        self.state.active_index = 2
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_drag_handle()
        operator.item_id = 'id-Idle'
        event = self.event('LEFTMOUSE', y=860, value='RELEASE')
        event.mouse_prev_press_y = 800
        self.assertEqual(operator.invoke(self.context, event), {'FINISHED'})
        self.assertEqual(operator._row_height, 60.0)
        self.backend.move.assert_called_once_with(self.context, 'id-Idle', -1)

    def test_tall_drag_handle_ignores_click_keyboard_and_changed_selection(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_drag_handle()
        operator.item_id = 'id-Walk'
        for kind, value, y in (('LEFTMOUSE', 'RELEASE', 800), ('LEFTMOUSE', 'PRESS', 750),
                               ('RET', 'PRESS', 750)):
            event = self.event(kind, y=y, value=value)
            event.mouse_prev_press_y = 800
            self.assertEqual(operator.invoke(self.context, event), {'CANCELLED'})
        self.state.active_index = 1
        event = self.event('LEFTMOUSE', y=750, value='RELEASE')
        event.mouse_prev_press_y = 800
        self.assertEqual(operator.invoke(self.context, event), {'CANCELLED'})
        self.backend.move.assert_not_called()

    def test_tall_drag_handle_draws_one_control_for_the_stable_selected_item(self):
        layout = Layout()
        self.ui.draw_worklist(layout, self.context)
        buttons = [value for kind, value, _enabled in layout.records
                   if kind == 'operator' and value[0] == 'character_designer.worklist_drag_handle']
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0][2].item_id, 'id-Walk')

    def test_drag_distance_accounts_for_dpi_and_sidebar_zoom(self):
        self.context.preferences.system.ui_scale = 1.5
        self.context.region.view2d = SimpleNamespace(region_to_view=lambda x, y: (x, y / 2.0))
        operator = self.drag()
        self.assertEqual(operator._row_height, 60.0)
        operator.modal(self.context, self.event('MOUSEMOVE', y=740))
        self.assertEqual(operator.modal(self.context, self.event('LEFTMOUSE', value='RELEASE')), {'FINISHED'})
        self.backend.move.assert_called_once_with(self.context, 'id-Walk', 1)

    def test_drag_cancel_and_changed_list_never_apply_a_move(self):
        operator = self.drag()
        operator.modal(self.context, self.event('MOUSEMOVE', y=750))
        self.assertEqual(operator.modal(self.context, self.event('ESC')), {'CANCELLED'})
        self.backend.move.assert_not_called()
        operator = self.drag()
        self.state.items.reverse()
        self.assertEqual(operator.modal(self.context, self.event('LEFTMOUSE', value='RELEASE')), {'CANCELLED'})
        self.backend.move.assert_not_called()
        self.assertFalse(self.ui._DRAGS)

    def test_stop_cancels_pending_drag_without_reordering(self):
        operator = self.drag()
        self.ui.stop_worklist_ui()
        self.assertEqual(operator.modal(self.context, self.event('LEFTMOUSE', value='RELEASE')), {'CANCELLED'})
        self.backend.move.assert_not_called()
        self.assertFalse(self.ui._DRAGS)

    def test_remove_only_dispatches_membership_operation(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_remove()
        operator.item_id = 'id-Run'
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.backend.remove.assert_called_once_with(self.context, 'id-Run')
        for name in ('activate', 'connect', 'refresh', 'add', 'move', 'sync'):
            getattr(self.backend, name).assert_not_called()

    def test_existing_animation_editors_redraw_without_rearranging_other_areas(self):
        kinds = ('VIEW_3D', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'NLA_EDITOR', 'PROPERTIES')
        areas = [SimpleNamespace(type=kind, tag_redraw=Mock()) for kind in kinds]
        self.context.screen.areas = areas
        self.ui._redraw(self.context)
        self.assertEqual(tuple(area.type for area in areas), kinds)
        for area in areas[:3]:
            area.tag_redraw.assert_called_once_with()
        for area in areas[3:]:
            area.tag_redraw.assert_not_called()
        self.context.screen = None
        self.ui._redraw(self.context)

    def test_reopened_worklist_plays_saved_suffixed_rig_with_a_mesh_selected(self):
        animation = self.load_playback()
        saved_rig = self.rig()
        self.state.rig = saved_rig
        self.context.scene.objects[saved_rig.name] = saved_rig
        self.context.object = self.rig('Body', kind='MESH')
        main_rig = self.rig('Configured Authoring Rig')
        self.context.scene.character_designer_setup = SimpleNamespace(rig=main_rig)
        self.context.scene.objects[main_rig.name] = main_rig
        for wm_target in (None, self.rig('OtherSceneRig')):
            with self.subTest(wm_target=wm_target):
                self.context.window_manager.character_designer_animation.target = wm_target
                self.assertIs(animation._target(self.context), saved_rig)
                self.assertTrue(animation.CHARACTERDESIGNER_OT_animation_play_pause.poll(self.context))
        self.context.screen = None
        self.assertFalse(animation.CHARACTERDESIGNER_OT_animation_play_pause.poll(self.context))

    def test_connected_worklist_never_falls_back_to_unrelated_or_invalid_rig(self):
        animation = self.load_playback()
        unrelated = self.rig('CoshaRig')
        sys.modules['bpy'].data.objects['CoshaRig'] = unrelated
        self.context.object = unrelated
        self.context.window_manager.character_designer_animation.target = unrelated
        self.context.scene.character_designer_setup = SimpleNamespace(rig=unrelated)
        invalid_rigs = (None, self.rig(), self.rig(kind='MESH'), self.rig(library=object()))
        for rig in invalid_rigs:
            with self.subTest(rig=rig):
                self.state.rig = rig
                self.context.scene.objects = {unrelated.name: unrelated}
                if rig is not None and (rig.type != 'ARMATURE' or rig.library is not None):
                    self.context.scene.objects[rig.name] = rig
                self.assertIsNone(animation._target(self.context))
                self.assertFalse(animation.CHARACTERDESIGNER_OT_animation_play_pause.poll(self.context))

    def test_unconnected_scene_keeps_legacy_target_precedence(self):
        animation = self.load_playback(connected=False)
        wm_rig, selected_rig, named_rig = (self.rig(name) for name in ('Explicit', 'Selected', 'CoshaRig'))
        self.context.window_manager.character_designer_animation.target = wm_rig
        self.context.object = selected_rig
        sys.modules['bpy'].data.objects['CoshaRig'] = named_rig
        self.assertIs(animation._target(self.context), wm_rig)
        self.context.window_manager.character_designer_animation.target = None
        self.assertIs(animation._target(self.context), selected_rig)
        self.context.object = self.rig('Body', kind='MESH')
        self.assertIs(animation._target(self.context), named_rig)
        sys.modules['bpy'].data.objects.clear()
        self.assertIsNone(animation._target(self.context))

    def test_configured_main_rig_precedes_every_legacy_animation_fallback(self):
        animation = self.load_playback(connected=False)
        main_rig, wm_rig, selected_rig, named_rig = (self.rig(name)
            for name in ('Main', 'Explicit', 'Selected', 'CoshaRig'))
        self.context.scene.character_designer_setup = SimpleNamespace(rig=main_rig)
        self.context.scene.objects = {rig.name: rig for rig in (main_rig, wm_rig, selected_rig, named_rig)}
        self.context.window_manager.character_designer_animation.target = wm_rig
        self.context.object = selected_rig
        sys.modules['bpy'].data.objects['CoshaRig'] = named_rig
        self.assertIs(animation._target(self.context), main_rig)
        self.assertTrue(animation.CHARACTERDESIGNER_OT_animation_play_pause.poll(self.context))
        self.assertIs(self.context.window_manager.character_designer_animation.target, wm_rig)
        self.assertIs(self.context.object, selected_rig)

    def test_invalid_configured_main_rig_blocks_legacy_fallback_without_clearing_choices(self):
        animation = self.load_playback(connected=False)
        fallback = self.rig('CoshaRig')
        self.context.window_manager.character_designer_animation.target = fallback
        self.context.object = fallback
        sys.modules['bpy'].data.objects['CoshaRig'] = fallback
        for invalid in (self.rig('Missing'), self.rig('Wrong Type', kind='MESH'),
                        self.rig('Linked', library=object()), self.rig('Duplicate Name')):
            with self.subTest(rig=invalid.name):
                setup = SimpleNamespace(rig=invalid)
                self.context.scene.character_designer_setup = setup
                self.context.scene.objects = {fallback.name: fallback}
                if invalid.type != 'ARMATURE' or invalid.library is not None:
                    self.context.scene.objects[invalid.name] = invalid
                if invalid.name == 'Duplicate Name':
                    self.context.scene.objects[invalid.name] = self.rig(invalid.name)
                self.assertIsNone(animation._target(self.context))
                self.assertFalse(animation.CHARACTERDESIGNER_OT_animation_play_pause.poll(self.context))
                self.assertIs(setup.rig, invalid)
                self.assertIs(self.context.window_manager.character_designer_animation.target, fallback)

    def draw_legacy_panel(self, animation):
        package_name = self.ui.__package__
        unity = ModuleType(package_name + '.unity_animation')
        unity.active_preview = Mock(return_value=None)
        unity.preview_time_seconds = Mock(return_value=0)
        link = ModuleType(package_name + '.animation_link')
        link.get_link = Mock(return_value=None)
        self.exporter.active_job.return_value = None
        layout = Layout()
        with patch.dict(sys.modules, {unity.__name__: unity, link.__name__: link}):
            animation.CHARACTERDESIGNER_PT_animation.draw(SimpleNamespace(layout=layout), self.context)
        return layout.records

    def test_main_rig_panel_hides_duplicate_controls_and_reports_invalid_choice_without_mutation(self):
        animation = self.load_playback(connected=False)
        main_rig, old_target = self.rig('Main'), self.rig('Old Animation Target')
        settings = self.context.window_manager.character_designer_animation
        settings.target, settings.status = old_target, ''
        settings.start_frame, settings.show_unity_files, settings.show_local_motion = 1, False, False
        setup = SimpleNamespace(rig=main_rig)
        self.context.scene.character_designer_setup = setup
        self.context.scene.objects = {main_rig.name: main_rig}
        self.context.object = old_target
        before = dict(vars(setup)), dict(vars(settings)), self.context.object, self.state.rig
        for valid in (True, False):
            with self.subTest(valid=valid):
                if not valid:
                    self.context.scene.objects.clear()
                records = self.draw_legacy_panel(animation)
                self.assertFalse(any(kind == 'prop' and value[0] == 'target' for kind, value, _ in records))
                labels = [value['text'] for kind, value, _ in records if kind == 'label']
                self.assertFalse(any(text.startswith('Using:') for text in labels))
                self.assertEqual(any('Rig > Character Setup' in text for text in labels), not valid)
                self.assertIn('character_designer.animation_unity_import',
                              [value[0] for kind, value, _ in records if kind == 'operator'])
                self.assertEqual((dict(vars(setup)), dict(vars(settings)), self.context.object, self.state.rig), before)

    def test_unset_main_rig_keeps_legacy_selector_and_automatic_target_label(self):
        animation = self.load_playback(connected=False)
        selected = self.rig('Selected')
        self.context.object = selected
        settings = self.context.window_manager.character_designer_animation
        settings.status = ''
        settings.start_frame, settings.show_unity_files, settings.show_local_motion = 1, False, False
        self.context.scene.character_designer_setup = SimpleNamespace(rig=None)
        records = self.draw_legacy_panel(animation)
        self.assertTrue(any(kind == 'prop' and value[0] == 'target' for kind, value, _ in records))
        self.assertIn('Using: Selected', [value['text'] for kind, value, _ in records if kind == 'label'])
        self.assertIsNone(settings.target)
        self.assertIsNone(self.context.scene.character_designer_setup.rig)

    def test_pending_export_does_not_change_or_redraw_scene_feedback(self):
        animation = self.load_animation()
        self.exporter.poll_export.return_value = None
        self.assertEqual(animation._poll_action_export(), 0.25)
        self.assertEqual(self.state.status, 'Syncing original scene')
        self.assertEqual(self.other_state.status, 'Other scene unchanged')
        self.assertIs(animation._export_window_manager, self.context.window_manager)
        animation._redraw.assert_not_called()

    def test_completed_export_updates_originating_scene_after_scene_switch(self):
        animation = self.load_animation()
        self.assertIsNone(animation._poll_action_export())
        self.assertIn('Synced Edited Walk.fbx', self.state.status)
        self.assertIn('Preview and Apply', self.state.status)
        self.assertFalse(self.state.has_error)
        self.assertEqual(self.other_state.status, 'Other scene unchanged')
        self.assertIsNone(animation._export_window_manager)
        self.exporter.cancel_export.assert_not_called()
        animation._redraw.assert_called_once_with()

    def test_failed_export_updates_originating_scene_after_scene_switch(self):
        animation = self.load_animation()
        self.exporter.poll_export.side_effect = ValueError('Worker failed before publication')
        self.assertIsNone(animation._poll_action_export())
        self.assertEqual(self.state.status, 'Worker failed before publication')
        self.assertTrue(self.state.has_error)
        self.assertEqual(self.other_state.status, 'Other scene unchanged')
        self.exporter.cancel_export.assert_called_once_with(self.job)
        self.assertIsNone(animation._export_window_manager)

    def test_deleted_origin_scene_does_not_break_export_cleanup(self):
        animation = self.load_animation()

        class DeletedScene:
            @property
            def character_designer_animation_worklist(self):
                raise ReferenceError('Scene was deleted')

        self.job['_worklist_scene'] = DeletedScene()
        self.assertIsNone(animation._poll_action_export())
        self.assertEqual(self.other_state.status, 'Other scene unchanged')
        self.assertIsNone(animation._export_window_manager)
        animation._redraw.assert_called_once_with()

    def test_cancel_export_updates_originating_scene_after_scene_switch(self):
        animation = self.load_animation()
        operator = animation.CHARACTERDESIGNER_OT_animation_export_cancel()
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.assertIn('cancelled', self.state.status)
        self.assertFalse(self.state.has_error)
        self.assertEqual(self.other_state.status, 'Other scene unchanged')
        self.assertIsNone(animation._export_window_manager)
        sys.modules['bpy'].app.timers.unregister.assert_called_once_with(animation._poll_action_export)

    def test_connected_panel_keeps_playback_and_cancel_without_legacy_controls(self):
        animation = self.load_animation()
        self.context.scene = self.origin_scene
        self.context.scene.frame_current = 7
        self.context.screen.is_animation_playing = False
        self.state.rig = self.state.items[0].rig
        layout = Layout()
        animation.CHARACTERDESIGNER_PT_animation.draw(SimpleNamespace(layout=layout), self.context)
        buttons = [value[0] for kind, value, _enabled in layout.records if kind == 'operator']
        self.assertIn('character_designer.animation_play_pause', buttons)
        self.assertIn('character_designer.animation_export_cancel', buttons)
        self.assertNotIn('character_designer.animation_link_import', buttons)
        self.assertNotIn('character_designer.animation_unity_import', buttons)
        self.assertNotIn('character_designer.animation_export', buttons)


if __name__ == '__main__':
    unittest.main()
