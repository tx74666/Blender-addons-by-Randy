"""Match modal contracts with native-shaped Events and an isolated fake WM.

Only bpy's container/registration boundary is replaced. The real modal and
file exchange run against temporary fixtures; no Blender or Unity is started.
"""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
fixture_spec = importlib.util.spec_from_file_location(
    'material_match_ui_fixture', ROOT / 'tests/test_unity_material_match.py')
fixtures = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixtures)


class Event:
    # Event RNA exposes type/value, not the Timer object returned by the WM.
    __slots__ = ('type', 'value')

    def __init__(self, event_type):
        self.type, self.value = event_type, 'NOTHING'


class Material:
    def __init__(self, name):
        self.name = name


class Entries(list):
    def add(self):
        entry = SimpleNamespace(material=None, source_material='', material_guid='',
                                material_local_id='', material_path='', material_name='',
                                project_root='', export_asset_id='')
        self.append(entry)
        return entry


class WindowManager:
    def __init__(self):
        self.timers, self.removed, self.handlers = [], [], []

    def event_timer_add(self, seconds, **_kwargs):
        timer = object()
        self.timers.append(timer)
        return timer

    def event_timer_remove(self, timer):
        self.removed.append(timer)

    def modal_handler_add(self, operator):
        self.handlers.append(operator)


class MaterialMatchUITests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.UnityMaterialMatchTests(
            'test_saved_recipe_or_result_without_receipt_never_claims_matched')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.material = Material(self.fixture.source)
        self.skin = Material('Skin')
        self.config = SimpleNamespace(directory=str(self.fixture.directory), filename='IgnoredLegacyName',
                                      asset_id=self.fixture.asset_id, simple_materials=Entries(),
                                      unity_material_matches=Entries())
        self.rig = SimpleNamespace(name='CoshaRig', library=None)
        self.current_rig = self.rig
        self.wm = WindowManager()
        self.context = SimpleNamespace(window_manager=self.wm, window=object(), body_name='Cosha')
        self.now, self.redraws = 1000.0, 0
        self.exporting = False
        self.objects = [SimpleNamespace(type='MESH', material_slots=[
            SimpleNamespace(material=self.material), SimpleNamespace(material=self.skin)])]

        name = '_material_match_ui_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(ROOT / 'addons/character_designer')]
        bpy, props, types = ModuleType('bpy'), ModuleType('bpy.props'), ModuleType('bpy.types')
        props.PointerProperty = props.StringProperty = lambda **_kwargs: None

        class Operator:
            def report(self, severity, text):
                self.reports.append((severity, text))

        types.Operator, types.PropertyGroup = Operator, type('PropertyGroup', (), {})
        bpy.types, bpy.props, bpy.context = types, props, self.context
        bpy.types.Material = Material
        bpy.data = SimpleNamespace(materials={self.material.name: self.material, self.skin.name: self.skin})
        bpy.path = SimpleNamespace(abspath=lambda path: str(path))
        self.exporter = ModuleType(name + '.unity_export')
        self.exporter.model_filename = lambda context, _rig: context.body_name + '.fbx'
        self.exporter.export_running = lambda: self.exporting
        self.exporter.collect_character = lambda *_args: {'objects': self.objects}
        export_ui = ModuleType(name + '.unity_export_ui')
        export_ui._config = lambda _context: (self.current_rig, self.config)
        export_ui._exporter = lambda: self.exporter
        export_ui._simple_material_entries = lambda config: [
            entry for entry in config.simple_materials if entry.material is not None]
        export_ui._redraw = self.redraw
        export_ui._short = lambda text, _limit: str(text)
        modules = {'bpy': bpy, 'bpy.props': props, 'bpy.types': types, name: package,
                   name + '.unity_material_match': fixtures.exchange,
                   name + '.unity_export': self.exporter, name + '.unity_export_ui': export_ui}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location(
            name + '.unity_material_match_ui', ROOT / 'addons/character_designer/unity_material_match_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ui)
        self.clock = patch.object(self.ui.time, 'monotonic', lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.ui.stop_matches)

    def redraw(self, _context):
        self.redraws += 1

    def operator(self, material=None):
        op = self.ui.CHARACTERDESIGNER_OT_unity_match_material()
        op.reports = []
        op.material_name = (material or self.material).name
        return op

    def start(self, material=None):
        op = self.operator(material)
        self.assertEqual(op.invoke(self.context, Event('LEFTMOUSE')), {'RUNNING_MODAL'})
        return op

    def tick(self, op, advance=.25):
        self.now += advance
        return op.modal(self.context, Event('TIMER'))

    def publish(self, op, **changes):
        result = self.fixture.matched_result(op._request)
        result['source_material'] = op.material_name
        result.update(changes)
        self.fixture.write_json(self.fixture.result_path(op._request), result)
        return result

    def seed_entry(self, **changes):
        entry = self.config.unity_material_matches.add()
        for key, value in self.fixture.binding.items():
            if key != 'artist_metadata':
                setattr(entry, key, str(value) if key == 'material_local_id' else value)
        entry.material = self.material
        for key, value in changes.items():
            setattr(entry, key, value)
        return entry

    def assert_finished(self, op):
        self.assertNotIn(op, self.ui._ACTIVE)
        self.assertIsNone(op._timer)
        self.assertEqual(len(self.wm.timers), len(self.wm.removed))

    def test_waiting_has_immediate_feedback_and_native_timer_event_completes(self):
        op = self.start()
        self.assertIn(op, self.ui._ACTIVE)
        self.assertIn(op, self.wm.handlers)
        self.assertGreater(self.redraws, 0)
        self.assertTrue(op.reports)
        self.assertEqual(self.tick(op), {'PASS_THROUGH'})
        self.publish(op)
        self.fixture.receipt()
        self.assertEqual(self.tick(op), {'FINISHED'})
        entry = self.config.unity_material_matches[0]
        self.assertEqual(entry.material_local_id, '1657602633327794031')
        self.assertEqual(self.ui.status(self.rig, self.config, entry)[0], 'Matched')
        self.assert_finished(op)

    def test_destination_body_name_and_profile_changes_cancel_without_replacing_saved_pair(self):
        entry = self.seed_entry()
        original = vars(entry).copy()
        initial = (self.config.directory, self.context.body_name, self.config.asset_id)
        for owner, field, value in ((self.config, 'directory', str(self.fixture.foreign_root / 'Assets')),
                                   (self.context, 'body_name', 'RenamedBody'),
                                   (self.config, 'asset_id', 'f' * 32)):
            with self.subTest(field=field):
                self.config.directory, self.context.body_name, self.config.asset_id = initial
                op = self.start()
                self.publish(op)
                setattr(owner, field, value)
                self.assertEqual(self.tick(op), {'CANCELLED'})
                self.assertEqual(vars(entry), original)
                cancel = self.fixture.result_path(op._request).with_name(op._request['request_id'] + '.cancel')
                self.assertTrue(cancel.exists())
                self.assert_finished(op)

    def test_ignored_legacy_filename_changes_do_not_cancel_the_current_body_match(self):
        op = self.start()
        self.assertEqual(op._request['model_path'], 'Assets/Characters/Cosha/Cosha.fbx')
        self.assertEqual(op._request['export_asset_id'], self.fixture.asset_id)
        self.config.filename = 'Legacy name must not choose another model'
        self.publish(op)
        self.assertEqual(self.tick(op), {'FINISHED'})
        self.fixture.receipt()
        entry = self.config.unity_material_matches[0]
        self.assertEqual(self.ui.status(self.rig, self.config, entry)[0], 'Matched')
        self.assert_finished(op)

    def test_explicit_context_is_used_by_profile_invoke_modal_status_and_draw(self):
        entry = self.seed_entry()
        self.fixture.receipt()
        warning = 'Material "Crystal_Blue" uses a custom shader; set it up in Unity.'

        class Layout:
            def separator(self): pass
            def label(self, **_kwargs): pass
            def box(self): return self
            def row(self, **_kwargs): return self
            def operator(self, *_args, **_kwargs): return SimpleNamespace()

        unrelated = SimpleNamespace(body_name='Another scene body')
        with patch.object(self.ui.bpy, 'context', unrelated):
            self.assertEqual(self.ui._profile(self.rig, self.config, self.context)[1], 'Cosha.fbx')
            self.assertNotEqual(self.ui.status(self.rig, self.config, entry)[0], 'Matched')
            self.assertEqual(self.ui.status(self.rig, self.config, entry, context=self.context)[0], 'Matched')
            self.assertTrue(self.ui.warning_matched(self.rig, self.config, warning, context=self.context))
            with patch.object(self.ui, 'status', wraps=self.ui.status) as status:
                self.ui.draw_matches(Layout(), self.context, self.rig, self.config, False)
                status.assert_called_once_with(self.rig, self.config, entry, context=self.context)
            op = self.start()
            self.publish(op)
            self.assertEqual(self.tick(op), {'FINISHED'})
            self.assert_finished(op)

    def test_equivalent_normalized_destination_does_not_cancel(self):
        op = self.start()
        self.config.directory = str(self.fixture.directory / 'ignored/..')
        self.publish(op)
        self.assertEqual(self.tick(op), {'FINISHED'})
        self.assert_finished(op)

    def test_rig_switch_material_rename_export_start_and_simplified_choice_cancel(self):
        initial_rig, initial_name = self.current_rig, self.material.name
        for change in ('rig', 'material', 'export', 'simplified'):
            with self.subTest(change=change):
                self.current_rig, self.material.name = initial_rig, initial_name
                self.exporting = False
                self.config.simple_materials.clear()
                op = self.start()
                self.publish(op)
                if change == 'rig':
                    self.current_rig = SimpleNamespace(name='SoldierRig', library=None)
                elif change == 'material':
                    self.material.name = 'RenamedMaterial'
                elif change == 'export':
                    self.exporting = True
                else:
                    self.config.simple_materials.add().material = self.material
                self.assertEqual(self.tick(op), {'CANCELLED'})
                self.assertFalse(self.config.unity_material_matches)
                self.assert_finished(op)

    def test_duplicate_request_does_not_cancel_the_original_wait(self):
        op = self.start()
        duplicate = self.operator()
        self.assertEqual(duplicate.invoke(self.context, Event('LEFTMOUSE')), {'CANCELLED'})
        self.assertEqual(self.ui._ACTIVE, [op])
        self.assertEqual(len(self.wm.timers), 1)
        self.assertFalse(self.fixture.result_path(op._request).with_name(op._request['request_id'] + '.cancel').exists())

    def test_escape_wins_over_late_success_and_keeps_saved_pair(self):
        entry = self.seed_entry()
        original = vars(entry).copy()
        op = self.start()
        self.publish(op, material_name='Replacement')
        self.assertEqual(op.modal(self.context, Event('ESC')), {'CANCELLED'})
        self.assertEqual(vars(entry), original)
        self.assertEqual(fixtures.exchange.read_result(op._request)['status'], 'cancelled')
        self.assert_finished(op)

    def test_cancel_disk_failure_still_returns_and_unregister_cleans_every_wait(self):
        first, second = self.start(), self.start(self.skin)
        with patch.object(fixtures.exchange, 'cancel_request', side_effect=OSError('Disk unavailable')):
            self.assertEqual(first.modal(self.context, Event('ESC')), {'CANCELLED'})
            self.ui.stop_matches()
            self.ui.stop_matches()
        self.assertTrue(any('WARNING' in severity for severity, _ in first.reports))
        self.assertTrue(any('WARNING' in severity for severity, _ in second.reports))
        self.assertFalse(self.ui._ACTIVE)
        self.assert_finished(first)
        self.assert_finished(second)

    def test_timeout_and_terminal_failure_recover_without_saving_a_pair(self):
        for outcome in ('timeout', 'failed', 'cancelled'):
            with self.subTest(outcome=outcome):
                op = self.start()
                if outcome == 'timeout':
                    self.now += fixtures.exchange.TIMEOUT + 1
                else:
                    self.publish(op, status=outcome, error='Unity terminal result')
                self.assertEqual(self.tick(op), {'CANCELLED'})
                self.assertFalse(self.config.unity_material_matches)
                self.assert_finished(op)

    def test_missing_result_name_is_rejected_before_saved_entry_mutation(self):
        entry = self.seed_entry()
        original = vars(entry).copy()
        op = self.start()
        result = self.publish(op)
        result.pop('material_name')
        self.fixture.write_json(self.fixture.result_path(op._request), result)
        self.assertEqual(self.tick(op), {'CANCELLED'})
        self.assertEqual(vars(entry), original)
        self.assert_finished(op)

    def test_unrelated_events_and_frequent_timers_do_not_repeat_polling(self):
        op = self.start()
        with patch.object(fixtures.exchange, 'read_result', return_value=None) as read:
            self.assertEqual(op.modal(self.context, Event('MOUSEMOVE')), {'PASS_THROUGH'})
            read.assert_not_called()
            self.assertEqual(self.tick(op), {'PASS_THROUGH'})
            self.assertEqual(self.tick(op, .01), {'PASS_THROUGH'})
            self.assertEqual(read.call_count, 1)
            self.assertEqual(self.tick(op, .25), {'PASS_THROUGH'})
            self.assertEqual(read.call_count, 2)

    def test_ui_status_warning_and_export_recipe_agree_and_simplified_preserves_pair(self):
        entry = self.seed_entry()
        original = vars(entry).copy()
        warning = 'Material "Crystal_Blue" uses a custom shader; set it up in Unity.'
        self.assertNotEqual(self.ui.status(self.rig, self.config, entry)[0], 'Matched')
        self.assertFalse(self.ui.warning_matched(self.rig, self.config, warning))
        self.fixture.receipt()
        self.assertEqual(self.ui.status(self.rig, self.config, entry)[0], 'Matched')
        self.assertTrue(self.ui.warning_matched(self.rig, self.config, warning))
        self.config.simple_materials.add().material = self.material
        self.assertEqual(self.ui.status(self.rig, self.config, entry)[0], 'Using Simplified')
        self.assertFalse(self.ui.warning_matched(self.rig, self.config, warning))
        self.assertEqual(self.ui.capture(self.config, self.objects, self.fixture.directory, self.fixture.asset_id), [])
        self.assertEqual(vars(entry), original)
        self.config.simple_materials.clear()  # Existing Use Original removes only this preference.
        recipe = self.ui.capture(self.config, self.objects, self.fixture.directory, self.fixture.asset_id)
        self.assertEqual(recipe[0]['material_local_id'], 1657602633327794031)
        self.assertEqual(self.ui.status(self.rig, self.config, entry)[0], 'Matched')
        entry.export_asset_id = 'f' * 32
        self.assertNotEqual(self.ui.status(self.rig, self.config, entry)[0], 'Matched')
        self.assertFalse(self.ui.warning_matched(self.rig, self.config, warning))
        with self.assertRaises(ValueError):
            self.ui.capture(self.config, self.objects, self.fixture.directory, self.fixture.asset_id)

    def test_unused_pair_is_retained_without_blocking_export(self):
        unused = Material('Unused')
        entry = self.seed_entry(material=unused, material_guid='invalid unused reference',
                                export_asset_id='f' * 32)
        original = vars(entry).copy()
        self.assertEqual(self.ui.capture(self.config, self.objects, self.fixture.directory, self.fixture.asset_id), [])
        self.assertEqual(vars(entry), original)


if __name__ == '__main__':
    unittest.main()
