"""Controller opening transactions with isolated native-shaped ownership stubs."""

import importlib.util
import math
from contextlib import contextmanager
import copy
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class Action:
    def __init__(self, name):
        self.name = name
        self.use_fake_user = False
        self.data = {'curves': ('retained source data',)}
        self.frame_range = (1.25, 24.2)

    @property
    def users(self):
        return int(self.use_fake_user)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __contains__(self, key):
        return key in self.data


class PropertyGroup(SimpleNamespace):
    def __init__(self, **fields):
        super().__init__(_custom={}, **fields)

    def get(self, key, default=None):
        return self._custom.get(key, default)

    def __contains__(self, key):
        return key in self._custom

    def __getitem__(self, key):
        return self._custom[key]

    def __setitem__(self, key, value):
        self._custom[key] = value

    def __delitem__(self, key):
        del self._custom[key]


class Record(PropertyGroup):
    def __init__(self):
        super().__init__(workspace_identity='', work_item_id='', clip_key='', target=None,
                         source_rig=None, source_action=None, source_slot=0, source_fingerprint='',
                         controller_action=None, controller_slot=0, alternate_action=None, alternate_slot=0,
                         state='UNTRIED', error='')

    def __setattr__(self, name, value):
        if getattr(self, 'fail_field', None) == name:
            super().__setattr__('fail_field', None)
            raise RuntimeError('Injected field write failure')
        super().__setattr__(name, value)


class Records(list):
    def add(self):
        record = Record()
        self.append(record)
        return record

    def remove(self, index):
        del self[index]


class Rig(SimpleNamespace):
    def __init__(self, name):
        super().__init__(name=name, type='ARMATURE', mode='OBJECT', library=None,
                         override_library=None, is_editable=True, selected=False, hidden=False,
                         data=SimpleNamespace(library=None, override_library=None), animation_data=None,
                         pose=SimpleNamespace(bones=[]), manual_pose='held unkeyed pose')

    def select_set(self, selected):
        self.selected = selected

    def hide_set(self, hidden):
        self.hidden = hidden

    def hide_get(self):
        return self.hidden

    def animation_data_clear(self):
        self.animation_data = None


class Objects(dict):
    active = None


class AnimationData(SimpleNamespace):
    @property
    def action_slot_handle(self):
        return self.action_slot.handle if self.action_slot else 0

    @action_slot_handle.setter
    def action_slot_handle(self, handle):
        if self.action_slot_handle != handle:
            self.action_slot = SimpleNamespace(handle=handle) if handle else None


class Context:
    def __init__(self, scene):
        self.window = SimpleNamespace(scene=scene)
        self.window_manager = SimpleNamespace(character_designer_animation=SimpleNamespace(target=None))
        self.playing = True
        self._overrides = {}

    @property
    def scene(self):
        return self._overrides.get('scene', self.window.scene)

    @property
    def view_layer(self):
        return self._overrides.get('view_layer', self.scene.view_layer)

    @property
    def mode(self):
        active = self.view_layer.objects.active
        return active.mode if active else 'OBJECT'

    @contextmanager
    def temp_override(self, **values):
        previous = self._overrides
        self._overrides = {**previous, **values}
        try:
            yield self
        finally:
            self._overrides = previous

    @property
    def selected_objects(self):
        return [obj for obj in self.view_layer.objects.values() if obj.selected]


class ControllerEdit(unittest.TestCase):
    def setUp(self):
        name = '_controller_edit_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        self.bpy = ModuleType('bpy')
        self.bpy.data = SimpleNamespace(actions=SimpleNamespace(remove=Mock()))
        self.work = ModuleType(name + '.animation_worklist')
        self.work._idle = Mock()
        self.work.activate = Mock()
        self.work.state = lambda context: context.scene.character_designer_animation_worklist
        self.edit = ModuleType(name + '.animation_worklist_edit_scene')
        self.mapping = ModuleType(name + '.animation_controller_mapping')
        self.mapping.MappingNeeded = type('MappingNeeded', (ValueError,), {})
        self.browser = ModuleType(name + '.animation_action_browser')
        self.browser.classify_action = Mock()
        self.local = ModuleType(name + '.animation_local_motion_pins')
        self.local.assign_character_action = Mock()
        self.inputs = ModuleType(name + '.animation_original_inputs_ui')
        self.inputs.original_inputs_live = Mock(return_value=False)
        self.composition = ModuleType(name + '.animation_composition')
        self.composition.active = Mock(return_value=False)
        self.ua = ModuleType(name + '.unity_animation')
        self.ua.active_preview = Mock(return_value=False)
        self.ua._set_playing = Mock(side_effect=lambda context, playing: setattr(context, 'playing', playing))
        def frame(scene, value):
            scene.frame_current = math.floor(value)
            scene.frame_subframe = value - scene.frame_current
            self.target.manual_pose = 'Action-evaluated pose'
        self.ua._set_frame = Mock(side_effect=frame)
        self.source_helpers = ModuleType(name + '.animation_link_source')
        def context_state(context):
            return dict(scene=context.scene, view_layer=context.view_layer, active=context.view_layer.objects.active,
                        selected=tuple(context.selected_objects), playing=context.playing)
        def restore_context(context, saved, *, playing):
            context.window.scene = saved['scene']
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in saved['selected']:
                obj.select_set(True)
            context.view_layer.objects.active = saved['active']
            if playing:
                context.playing = saved['playing']
        self.source_helpers._context_state = context_state
        self.source_helpers._restore_context = Mock(side_effect=restore_context)
        self.controller = ModuleType(name + '.controller_animation')
        self.controller._raw = Mock(side_effect=lambda rig, _names: rig.manual_pose)
        self.controller._restore_raw = Mock(side_effect=lambda rig, pose: setattr(rig, 'manual_pose', pose))
        self.natural = ModuleType(name + '.natural_bind_mode')
        self.natural.ExpiryCommitError = type('ExpiryCommitError', (RuntimeError,), {'committed': True})
        self.transition = SimpleNamespace(commit=Mock())
        self.deferral_depth = 0
        @contextmanager
        def deferral(_rig):
            self.deferral_depth += 1
            try:
                yield self.transition
            finally:
                self.deferral_depth -= 1
        self.natural.defer_expiry = Mock(side_effect=deferral)
        self.target = Rig('CoshaRig')
        self.previous_action = Action('Previous Character Action')
        self.previous_slot = SimpleNamespace(handle=13)
        self.target.animation_data = AnimationData(action=self.previous_action, action_slot=self.previous_slot,
                                                  use_nla=True, action_blend_type='ADD', action_influence=.35)
        def assign(rig, action, slot):
            if rig.animation_data is None:
                rig.animation_data = AnimationData(action_slot=None, use_nla=True, action_blend_type='ADD', action_influence=.35)
            rig.animation_data.action = action
            rig.animation_data.action_slot = SimpleNamespace(handle=slot)
        self.local.assign_character_action.side_effect = assign
        self.assign = assign
        self.row = SimpleNamespace(item_id='item-walk', work_item_id='work-walk', clip_key='clip-walk',
                                   name='Walk Forward', rig=Rig('Source Rig'),
                                   custom_action=Action('Walk Native Custom'), custom_slot=7)
        self.records = Records()
        self.home = SimpleNamespace(
            library=None, override_library=None, objects={'CoshaRig': self.target},
            character_designer_setup=SimpleNamespace(rig=self.target),
            character_designer_controller_motions=self.records,
            character_designer_animation_worklist=PropertyGroup(
                workspace_identity='workspace', convert_imported_to_controls=True,
                target_name='Cosha', catalog=[], items=[], catalog_index=0, active_index=-1, status='', has_error=False))
        self.source = SimpleNamespace(render=SimpleNamespace(fps=24, fps_base=1.0),
            character_designer_animation_worklist=SimpleNamespace(items=[self.row], catalog=[], active_index=0, list_cursor=0, status='', has_error=False))
        self.other = Rig('Other Selection')
        self.other.selected = True
        self.target.hidden = True
        for scene, objects in ((self.home, [self.target, self.other]), (self.source, [self.row.rig])):
            scene.view_layer = SimpleNamespace(objects=Objects((obj.name, obj) for obj in objects), update=Mock())
            scene.view_layer.objects.active = objects[-1]
            scene.view_layers = [scene.view_layer]
            scene.frame_start, scene.frame_end = 5, 80
            scene.frame_preview_start, scene.frame_preview_end = 7, 75
            scene.frame_current, scene.frame_subframe = 26, .5
            scene.use_preview_range = False
            scene.tool_settings = SimpleNamespace(use_keyframe_insert_auto=True)
        self.context = Context(self.home)
        self.bpy.context = self.context
        self.bpy.data.scenes = [self.home, self.source]
        self.edit.editing_scene = Mock(return_value=self.source)
        self.edit.controller_only = Mock(return_value=False)
        self.edit._local_row = Mock(return_value=self.row)
        self.work.add = Mock(return_value=self.row)
        self.built = SimpleNamespace(action=Action('Built Controller'), slot=42,
                                     source_fingerprint='source-proof')
        self.mapping.build = Mock(return_value=self.built)
        modules = {name: package, 'bpy': self.bpy}
        for module in (self.work, self.edit, self.mapping, self.browser, self.local, self.inputs,
                       self.composition, self.ua, self.source_helpers, self.natural):
            modules[module.__name__] = module
            setattr(package, module.__name__.rsplit('.', 1)[1], module)
        modules[self.controller.__name__] = self.controller
        setattr(package, 'controller_animation', self.controller)
        mocked = patch.dict(sys.modules, modules)
        mocked.start()
        self.addCleanup(mocked.stop)
        spec = importlib.util.spec_from_file_location(name + '.animation_controller_edit',
                                                      SOURCE / 'animation_controller_edit.py')
        self.service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.service)

    def open(self):
        return self.service.open_motion(self.context, self.home, 'clip-walk')

    def open_source(self):
        return self.service.open_source_motion(self.context, self.home, 'clip-walk')

    def opening_state(self):
        return dict(
            context=self.source_helpers._context_state(self.context),
            binding=vars(self.target.animation_data).copy(),
            pose=self.target.manual_pose, hidden=self.target.hidden,
            selection=tuple((obj, obj.selected) for obj in (self.target, self.other, self.row.rig)),
            scene_state=tuple((scene.view_layer.objects.active, scene.frame_current, scene.frame_subframe,
                               scene.frame_start, scene.frame_end, scene.frame_preview_start,
                               scene.frame_preview_end, scene.use_preview_range,
                               scene.tool_settings.use_keyframe_insert_auto)
                              for scene in (self.home, self.source)),
            catalog_index=self.home.character_designer_animation_worklist.catalog_index,
            source_index=self.source.character_designer_animation_worklist.active_index,
            animation_target=self.context.window_manager.character_designer_animation.target,
            source_action=copy.deepcopy(self.row.custom_action.data),
            records=[vars(record).copy() for record in self.records])

    def assert_source_preserved(self):
        self.assertEqual(self.row.custom_action.name, 'Walk Native Custom')
        self.assertEqual(self.row.custom_action.data, {'curves': ('retained source data',)})
        self.assertEqual(self.row.custom_slot, 7)
        self.assertEqual(self.row.rig.name, 'Source Rig')

    def ready(self):
        record = self.service._record(self.home, self.row)
        self.service._publish(record, self.row, self.built)
        self.built.action.use_fake_user = True
        return record

    def test_elbow_alignment_queues_the_retained_ik_action_independently_of_knees(self):
        self.row.source_action = Action('Imported Source')
        record = self.ready()
        record.controller_action.data['character_designer_knee_pole_layout'] = 1
        self.assertFalse(self.service.needs_knee_alignment(record))
        self.assertTrue(self.service.needs_elbow_alignment(record))
        with patch.object(self.service, '_enqueue') as enqueue:
            self.service.prepare_motion(self.context, self.home, self.row.clip_key, align_elbows=True)
            enqueue.assert_called_once_with(self.context, self.home, [self.row],
                                            retry=True, daily_edit=False, pole_alignment='elbows')
        record.controller_action.data['character_designer_elbow_pole_layout'] = 1
        with self.assertRaisesRegex(ValueError, 'elbow pole'):
            self.service.prepare_motion(self.context, self.home, self.row.clip_key, align_elbows=True)

    def test_elbow_replacement_reuses_the_working_name_and_retains_the_prior_action(self):
        record = self.ready()
        old = record.controller_action
        old.name = 'In Air'
        self.target.animation_data.action_slot_handle = 13
        result = SimpleNamespace(action=Action('Worker Output'), slot=42)
        result.action.data['character_designer_elbow_pole_layout'] = 1
        self.service.replace_aligned(self.context, self.home, record, (old, 42), result, kind='elbows')
        self.assertEqual((record.controller_action, record.controller_slot), (result.action, 42))
        self.assertEqual(result.action.name, 'In Air')
        self.assertTrue(old.name.startswith('.Archive'))
        self.assertTrue(old.use_fake_user)
        self.local.assign_character_action.assert_not_called()

    def test_failed_elbow_publication_restores_the_previous_names_and_binding(self):
        record = self.ready()
        old = record.controller_action
        old.name = 'In Air'
        self.target.animation_data.action_slot_handle = 13
        result = SimpleNamespace(action=Action('Worker Output'), slot=42)
        result.action.data['character_designer_elbow_pole_layout'] = 1
        record.fail_field = 'controller_slot'
        with self.assertRaisesRegex(RuntimeError, 'Injected'):
            self.service.replace_aligned(self.context, self.home, record, (old, 42), result, kind='elbows')
        self.assertEqual((record.controller_action, record.controller_slot), (old, 42))
        self.assertEqual((old.name, result.action.name), ('In Air', 'Worker Output'))
        self.local.assign_character_action.assert_not_called()

    def test_unprepared_open_preserves_home_or_source_and_never_imports_or_prepares(self):
        with patch.object(self.service, '_resolve_row', side_effect=AssertionError('No import lookup')) as resolve, \
                patch.object(self.service, '_native', side_effect=AssertionError('No native open')) as native, \
                patch.object(self.service, 'prepare_motion', side_effect=AssertionError('No preparation')) as prepare, \
                patch.object(self.service, 'prepare_missing', side_effect=AssertionError('No bulk preparation')) as bulk, \
                patch('subprocess.Popen', side_effect=AssertionError('No process launch')) as launch:
            for scene in (self.home, self.source):
                for enabled in (False, True):
                    for asynchronous in (False, True):
                        with self.subTest(scene=scene is self.home, legacy=enabled, asynchronous=asynchronous):
                            self.home.character_designer_animation_worklist.convert_imported_to_controls = enabled
                            self.context.window.scene = scene
                            before = self.opening_state()
                            result = self.service.open_motion(self.context, self.home, 'clip-walk', asynchronous=asynchronous)
                            self.assertEqual(result['state'], 'PREPARE_REQUIRED')
                            self.assertIn('Click Prepare', result['message'])
                            self.assertEqual(self.opening_state(), before)
            for callback in (resolve, native, prepare, bulk, launch):
                callback.assert_not_called()
        self.work._idle.assert_not_called()
        self.work.add.assert_not_called()
        self.work.activate.assert_not_called()
        self.mapping.build.assert_not_called()
        self.assert_source_preserved()

    def test_ready_open_ignores_legacy_checkbox_and_resumes_existing_edits(self):
        record = self.ready()
        self.built.action.data['artist_edit'] = 'retained'
        self.home.character_designer_animation_worklist.convert_imported_to_controls = False
        self.assertEqual(self.open()['state'], 'CONTROLLER')
        self.assertEqual(self.open()['state'], 'CONTROLLER')
        self.assertIs(self.records[0], record)
        self.assertEqual(self.built.action.data['artist_edit'], 'retained')
        self.mapping.build.assert_not_called()
        self.work.activate.assert_not_called()
        self.assert_source_preserved()

    def test_mapping_needed_open_does_not_retry_or_erase_reason(self):
        record = self.service._record(self.home, self.row)
        record.state, record.error = 'MAPPING_NEEDED', 'Missing native body mapping'
        before = self.opening_state()
        result = self.open()
        self.assertEqual(result['state'], 'PREPARE_REQUIRED')
        self.assertIn('Click Prepare', result['message'])
        self.assertIn(record.error, result['message'])
        self.assertEqual(self.opening_state(), before)
        self.assertEqual(record.error, 'Missing native body mapping')
        self.assertEqual(record.state, 'MAPPING_NEEDED')
        self.mapping.build.assert_not_called()
        self.work.add.assert_not_called()
        self.work.activate.assert_not_called()

    def test_catalog_open_stays_in_caller_without_importing(self):
        self.source.character_designer_animation_worklist.items = []
        self.edit._local_row.return_value = None
        self.home.character_designer_animation_worklist.catalog = [
            SimpleNamespace(clip_key='clip-walk', work_item_id='work-walk', ready=True)]
        for scene in (self.home, self.source):
            self.context.window.scene = scene
            before = self.opening_state()
            self.assertEqual(self.open()['state'], 'PREPARE_REQUIRED')
            self.assertEqual(self.opening_state(), before)
        self.work.add.assert_not_called()
        self.work.activate.assert_not_called()
        self.mapping.build.assert_not_called()
        self.local.assign_character_action.assert_not_called()
        self.assertEqual(self.records, [])

    def test_catalog_source_open_explicitly_imports_without_controller_conversion(self):
        self.source.character_designer_animation_worklist.items = []
        self.edit._local_row.return_value = None
        self.home.character_designer_animation_worklist.catalog = [SimpleNamespace(clip_key='clip-walk', ready=True)]
        self.service.return_to_character = Mock(side_effect=lambda context, home: setattr(context.window, 'scene', home))
        self.assertEqual(self.open_source()['state'], 'NATIVE')
        self.work.add.assert_called_once_with(self.context, 'clip-walk', activate_new=False, prepare_controls=False)
        self.work.activate.assert_called_once_with(self.context, self.row.item_id, 'CUSTOM')
        self.assertIs(self.context.scene, self.source)
        self.mapping.build.assert_not_called()
        self.local.assign_character_action.assert_not_called()
        self.assertEqual(self.records, [])

    def test_explicit_source_open_ignores_ready_character_edit_and_retains_it(self):
        record = self.ready()
        self.built.action.data['artist_edit'] = 'retained'
        before_binding = vars(self.target.animation_data).copy()
        self.assertEqual(self.open_source()['state'], 'NATIVE')
        self.assertIs(self.context.scene, self.source)
        self.assertEqual(vars(self.target.animation_data), before_binding)
        self.assertIs(self.records[0], record)
        self.assertEqual(self.built.action.data['artist_edit'], 'retained')
        self.work.activate.assert_called_once_with(self.context, self.row.item_id, 'CUSTOM')
        self.local.assign_character_action.assert_not_called()
        self.mapping.build.assert_not_called()
        self.assert_source_preserved()

    def test_ready_catalog_result_opens_without_reimporting_missing_source_row(self):
        self.ready()
        self.source.character_designer_animation_worklist.items = []
        self.home.character_designer_animation_worklist.catalog = [
            SimpleNamespace(clip_key='clip-walk', work_item_id='work-walk', ready=True)]
        self.assertEqual(self.open()['state'], 'CONTROLLER')
        self.assertIs(self.target.animation_data.action, self.built.action)
        self.work.add.assert_not_called()
        self.mapping.build.assert_not_called()

    def test_missing_or_ambiguous_rows_are_rejected_without_import_or_context_change(self):
        before = self.opening_state()
        for rows, catalog, message in (
                ([self.row, self.row], [], 'multiple source work items'),
                ([], [], 'no longer in the prepared list'),
                ([], [SimpleNamespace(clip_key='clip-walk', ready=True)] * 2, 'multiple catalog entries')):
            self.source.character_designer_animation_worklist.items = rows
            self.home.character_designer_animation_worklist.catalog = catalog
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.open()
            self.assertEqual(self.opening_state(), before)
            self.assertFalse(self.service._OPENING)
        self.work.add.assert_not_called()
        self.work.activate.assert_not_called()
        self.mapping.build.assert_not_called()

    def test_nested_native_open_is_rejected_and_guard_released(self):
        self.work.activate.side_effect = lambda *_args: self.open()
        self.service.return_to_character = Mock(side_effect=lambda context, home: setattr(context.window, 'scene', home))
        with self.assertRaisesRegex(RuntimeError, 'Finish opening the current motion'):
            self.open_source()
        self.assertFalse(self.service._OPENING)
        self.work.activate.side_effect = None
        self.assertEqual(self.open_source()['state'], 'NATIVE')
        self.mapping.build.assert_not_called()

    def test_native_activation_errors_restore_callers_scene_in_both_routes(self):
        for conversion in (False, True):
            with self.subTest(conversion=conversion):
                caller = SimpleNamespace(name='Caller Scene')
                self.context.window.scene = caller
                self.home.character_designer_animation_worklist.convert_imported_to_controls = conversion
                self.mapping.build.side_effect = self.mapping.MappingNeeded('Mapping unavailable')
                self.work.activate.side_effect = RuntimeError('Native activation failed')
                with self.assertRaisesRegex(RuntimeError, 'Native activation failed'):
                    self.open_source()
                self.assertIs(self.context.scene, caller)
                self.assert_source_preserved()


    def test_native_failure_returns_authenticated_home_through_input_preservation(self):
        self.home.character_designer_animation_worklist.convert_imported_to_controls = False
        self.work.activate.side_effect = RuntimeError('Native activation failed')
        self.service.return_to_character = Mock(side_effect=lambda context, home: setattr(context.window, 'scene', home))
        with self.assertRaisesRegex(RuntimeError, 'Native activation failed'):
            self.open_source()
        self.service.return_to_character.assert_called_once_with(self.context, self.home)
        self.assertIs(self.context.scene, self.home)
        self.assert_source_preserved()


    def test_provisional_failure_restores_binding_before_expiry_deferral_releases(self):
        self.ready()
        observed = []
        original_restore = self.source_helpers._restore_context.side_effect
        def restore(context, saved, *, playing):
            observed.append((self.deferral_depth, self.target.animation_data.action))
            return original_restore(context, saved, playing=playing)
        self.source_helpers._restore_context.side_effect = restore
        self.home.view_layer.update.side_effect = RuntimeError('Provisional pose failure')
        with self.assertRaisesRegex(RuntimeError, 'Provisional pose failure'):
            self.open()
        self.assertTrue(observed)
        self.assertTrue(all(depth == 1 and action is self.previous_action for depth, action in observed))
        self.assertEqual(self.deferral_depth, 0)
        self.transition.commit.assert_not_called()


    def test_terminal_refresh_failure_is_committed_recovery_and_retry_reuses_output(self):
        self.ready()
        self.home.view_layer.update.side_effect = [None, RuntimeError('Terminal refresh failed')]
        result = self.open()
        self.assertEqual(result['state'], 'CONTROLLER_RECOVERY')
        self.assertIn('Open this motion again', result['message'])
        self.assertIs(self.target.animation_data.action, self.built.action)
        self.assertEqual(self.records[0].state, 'READY')
        self.assertTrue(self.home.character_designer_animation_worklist.has_error)
        self.transition.commit.assert_called_once_with()
        self.home.view_layer.update.side_effect = None
        self.assertEqual(self.open()['state'], 'CONTROLLER')
        self.mapping.build.assert_not_called()
        self.assertFalse(self.home.character_designer_animation_worklist.has_error)


    def test_failed_activation_evaluates_restored_home_inputs_before_releasing_guard(self):
        self.ready()
        calls = []
        def update():
            calls.append((self.context.scene, self.deferral_depth, self.target.animation_data.action,
                          self.target.manual_pose))
            if len(calls) == 1:
                raise RuntimeError('One-shot provisional update failure')
        self.home.view_layer.update.side_effect = update
        with self.assertRaisesRegex(RuntimeError, 'One-shot provisional update failure'):
            self.open()
        self.assertEqual(len(calls), 2)
        scene, depth, action, pose = calls[1]
        self.assertIs(scene, self.home)
        self.assertEqual(depth, 1)
        self.assertIs(action, self.previous_action)
        self.assertEqual(pose, 'held unkeyed pose')
        self.transition.commit.assert_not_called()


    def test_committed_expiry_error_retains_selected_action_with_recovery_result(self):
        self.ready()
        @contextmanager
        def failure(_rig):
            yield self.transition
            raise self.natural.ExpiryCommitError('Committed expiry needs recovery')
        self.natural.defer_expiry.side_effect = failure
        result = self.open()
        self.assertEqual(result['state'], 'CONTROLLER_RECOVERY')
        self.assertIs(self.target.animation_data.action, self.built.action)
        self.assertEqual(self.records[0].state, 'READY')
        self.assertTrue(self.home.character_designer_animation_worklist.has_error)


    def test_activation_failure_retains_complete_result_for_later_reopen(self):
        self.ready()
        self.context.window.scene = self.source
        self.service.return_to_character = Mock(side_effect=lambda context, home: setattr(context.window, 'scene', home))
        self.local.assign_character_action.side_effect = RuntimeError('NLA context unavailable')
        with self.assertRaisesRegex(RuntimeError, 'NLA context unavailable'):
            self.open()
        self.assertIs(self.context.scene, self.source)
        self.assertEqual(self.records[0].state, 'READY')
        self.assertIs(self.records[0].controller_action, self.built.action)
        self.bpy.data.actions.remove.assert_not_called()
        self.local.assign_character_action.side_effect = self.assign
        self.assertEqual(self.open()['state'], 'CONTROLLER')
        self.mapping.build.assert_not_called()
        self.assertEqual(self.service.return_to_character.call_count, 2)
        self.assert_source_preserved()


    def test_duplicate_identity_is_rejected_without_creating_another_record(self):
        record = self.service._record(self.home, self.row)
        duplicate = self.records.add()
        vars(duplicate).update(vars(record))
        with self.assertRaisesRegex(ValueError, 'multiple saved controller edits'):
            self.open()
        self.assertEqual(len(self.records), 2)
        self.mapping.build.assert_not_called()
        self.work.activate.assert_not_called()
        self.assert_source_preserved()


    def test_import_failure_restores_callers_scene(self):
        self.edit._local_row.return_value = None
        self.home.character_designer_animation_worklist.catalog = [SimpleNamespace(clip_key='clip-walk', ready=True)]
        self.work.add.side_effect = RuntimeError('Import failed')
        self.service.return_to_character = Mock(side_effect=lambda context, home: setattr(context.window, 'scene', home))
        with self.assertRaisesRegex(RuntimeError, 'Import failed'):
            self.open_source()
        self.assertIs(self.context.scene, self.home)
        self.mapping.build.assert_not_called()
        self.assertEqual(len(self.records), 0)
        self.service.return_to_character.assert_called_once_with(self.context, self.home)


    def test_incomplete_saved_output_is_retained_and_refused_on_reopen(self):
        self.ready()
        self.open()
        record = self.records[0]
        record.state = 'UNTRIED'
        self.mapping.build.reset_mock()
        self.local.assign_character_action.reset_mock()
        with self.assertRaisesRegex(ValueError, 'saved controller edit is incomplete'):
            self.open()
        self.mapping.build.assert_not_called()
        self.local.assign_character_action.assert_not_called()
        self.assertIs(record.controller_action, self.built.action)
        self.assert_source_preserved()


    def test_reopen_refuses_nonlocal_or_removed_target_and_retains_output(self):
        self.ready()
        self.open()
        self.mapping.build.reset_mock()
        self.local.assign_character_action.reset_mock()
        for invalid in ('linked', 'removed'):
            with self.subTest(invalid=invalid):
                self.target.library = object() if invalid == 'linked' else None
                self.home.objects = {} if invalid == 'removed' else {'CoshaRig': self.target}
                with self.assertRaisesRegex(ValueError, 'locally editable character'):
                    self.open()
                self.assertEqual(self.records[0].state, 'READY')
                self.assertIs(self.records[0].controller_action, self.built.action)
                self.assert_source_preserved()
        self.mapping.build.assert_not_called()
        self.local.assign_character_action.assert_not_called()


    def test_open_sets_clip_range_first_pose_selection_and_unblended_playback(self):
        self.ready()
        self.assertEqual(self.open()['state'], 'CONTROLLER')
        ad = self.target.animation_data
        self.assertEqual((ad.use_nla, ad.action_blend_type, ad.action_influence), (False, 'REPLACE', 1.0))
        self.assertEqual((self.home.frame_start, self.home.frame_end), (1, 25))
        self.assertEqual((self.home.frame_preview_start, self.home.frame_preview_end), (1, 25))
        self.assertEqual((self.home.frame_current, self.home.frame_subframe), (1, .25))
        self.assertTrue(self.home.use_preview_range)
        self.assertFalse(self.home.tool_settings.use_keyframe_insert_auto)
        self.assertFalse(self.context.playing)
        self.assertFalse(self.target.hidden)
        self.assertIs(self.context.view_layer.objects.active, self.target)
        self.assertEqual(self.context.selected_objects, [self.target])
        self.assertIs(self.context.window_manager.character_designer_animation.target, self.target)
        self.assert_source_preserved()


    def test_post_binding_failure_restores_context_frame_range_nla_selection_and_prior_action(self):
        self.ready()
        self.service.return_to_character = Mock(side_effect=lambda context, home: setattr(context.window, 'scene', home))
        self.context.window.scene = self.source
        before = vars(self.target.animation_data).copy()
        original_context = self.source_helpers._context_state(self.context)
        self.home.view_layer.update.side_effect = RuntimeError('Injected evaluation failure')
        with self.assertRaisesRegex(RuntimeError, 'Injected evaluation failure'):
            self.open()
        self.assertEqual(vars(self.target.animation_data), before)
        self.assertEqual((self.home.frame_start, self.home.frame_end), (5, 80))
        self.assertEqual((self.home.frame_preview_start, self.home.frame_preview_end), (7, 75))
        self.assertEqual((self.home.frame_current, self.home.frame_subframe), (26, .5))
        self.assertFalse(self.home.use_preview_range)
        self.assertTrue(self.home.tool_settings.use_keyframe_insert_auto)
        self.assertTrue(self.context.playing)
        self.assertTrue(self.target.hidden)
        self.assertIs(self.home.view_layer.objects.active, self.other)
        self.assertTrue(self.other.selected)
        self.assertFalse(self.target.selected)
        self.assertEqual(self.source_helpers._context_state(self.context), original_context)
        self.assertIsNone(self.context.window_manager.character_designer_animation.target)
        self.assertIs(self.records[0].controller_action, self.built.action)
        self.assertFalse(self.previous_action.use_fake_user)
        self.assertEqual(self.target.manual_pose, 'held unkeyed pose')
        self.assert_source_preserved()


    def test_failure_clears_new_animation_data_when_target_had_none(self):
        self.ready()
        self.target.animation_data = None
        self.home.view_layer.update.side_effect = RuntimeError('Injected evaluation failure')
        with self.assertRaisesRegex(RuntimeError, 'Injected evaluation failure'):
            self.open()
        self.assertIsNone(self.target.animation_data)
        self.assertTrue(self.context.playing)
        self.assert_source_preserved()


    def test_controller_open_selects_home_catalog_without_misrepresenting_source_binding(self):
        self.ready()
        self.home.character_designer_animation_worklist.catalog = [
            SimpleNamespace(clip_key='other'), SimpleNamespace(clip_key='clip-walk')]
        self.source.character_designer_animation_worklist.active_index = 5
        self.assertEqual(self.open()['state'], 'CONTROLLER')
        self.assertEqual(self.home.character_designer_animation_worklist.catalog_index, 1)
        self.assertEqual(self.source.character_designer_animation_worklist.active_index, 5)
        self.assert_source_preserved()



if __name__ == '__main__':
    unittest.main()
