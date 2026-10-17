"""Source-watch scheduling and explicit refresh freshness without starting Blender."""
import ast
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/__init__.py'
if not SOURCE.is_file():
    SOURCE = Path(__file__).with_name('__init__.py')


def subject(source_root):
    timers = set()
    registrations = []

    def register(callback, **kwargs):
        timers.add(callback)
        registrations.append((callback, kwargs))

    namespace = {
        'os': os, 'ADDON_SOURCE_ROOT': str(source_root),
        'ADDON_REFRESH_PENDING': False, 'ADDON_REFRESH_LAST_STATE': False,
        'ADDON_REFRESH_LAST_ERROR': '', 'ADDON_LOADED_SIGNATURE': (),
        'Operator': type('Operator', (), {}), '_tag_view3d_redraw': Mock(),
        '_reload_addon_deferred': Mock(),
        'bpy': NS(context=NS(window_manager=NS(windows=[], is_interface_locked=False)),
                  app=NS(timers=NS(is_registered=lambda callback: callback in timers,
                                  register=register, unregister=timers.remove))),
    }
    functions = {'_source_files', '_source_signature', '_source_changed', '_refresh_ui_visible',
                 '_validate_source_files', '_source_watch_busy', '_source_watch_deferred',
                 '_register_source_watch', '_unregister_source_watch'}
    nodes = []
    for node in ast.parse(SOURCE.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == 'CHARACTERDESIGNER_OT_refresh_addon':
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'SOURCE_WATCH_INTERVAL'
                for target in node.targets):
            nodes.append(node)
    assert len(nodes) == len(functions) + 2
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    namespace['ADDON_LOADED_SIGNATURE'] = namespace['_source_signature']()
    return namespace, timers, registrations


class SourceWatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / '__init__.py'
        self.source.write_text('value = 1\n', encoding='utf-8')
        self.n, self.timers, self.registrations = subject(self.root)

    def tick(self):
        return self.n['_source_watch_deferred']()

    def operator(self):
        operator = self.n['CHARACTERDESIGNER_OT_refresh_addon']()
        operator.report = Mock()
        return operator

    def test_idle_checks_use_ten_second_persistent_timer_without_duplicate_registration(self):
        self.assertTrue(self.n['_register_source_watch']())
        self.assertTrue(self.n['_register_source_watch']())
        self.assertEqual(len(self.registrations), 1)
        self.assertEqual(self.registrations[0][1], {'first_interval': 10.0, 'persistent': True})
        self.assertEqual(self.tick(), 10.0)
        self.assertFalse(self.n['ADDON_REFRESH_LAST_STATE'])

    def test_playback_in_another_window_defers_filesystem_work(self):
        self.n['bpy'].context.window_manager.windows = [
            NS(screen=NS(is_animation_playing=False)), NS(screen=None),
            NS(screen=NS(is_animation_playing=True)),
        ]
        probe = self.n['_source_changed'] = Mock()
        self.assertEqual(self.tick(), 10.0)
        probe.assert_not_called()
        self.n['_tag_view3d_redraw'].assert_not_called()

    def test_interface_lock_defers_filesystem_work(self):
        self.n['bpy'].context.window_manager.is_interface_locked = True
        probe = self.n['_source_changed'] = Mock()
        self.assertEqual(self.tick(), 10.0)
        probe.assert_not_called()

    def test_restricted_context_does_not_stop_persistent_watch(self):
        self.n['bpy'].context = NS()
        (self.root / 'new_module.py').write_text('added = True\n', encoding='utf-8')
        self.assertEqual(self.tick(), 10.0)
        self.assertTrue(self.n['ADDON_REFRESH_LAST_STATE'])

    def test_pending_reload_retains_status_without_rescanning(self):
        self.n.update(ADDON_REFRESH_PENDING=True, ADDON_REFRESH_LAST_STATE=True,
                      ADDON_REFRESH_LAST_ERROR='Retain this refresh failure.')
        probe = self.n['_source_changed'] = Mock()
        self.assertEqual(self.tick(), 10.0)
        probe.assert_not_called()
        self.assertTrue(self.n['_refresh_ui_visible']())
        self.assertEqual(self.n['ADDON_REFRESH_LAST_ERROR'], 'Retain this refresh failure.')

    def test_first_idle_tick_after_playback_detects_change_then_reversion(self):
        screen = NS(is_animation_playing=True)
        self.n['bpy'].context.window_manager.windows = [NS(screen=screen)]
        new_file = self.root / 'new_module.py'
        new_file.write_text('added = True\n', encoding='utf-8')
        self.tick()
        self.assertFalse(self.n['ADDON_REFRESH_LAST_STATE'])
        screen.is_animation_playing = False
        self.tick()
        self.assertTrue(self.n['ADDON_REFRESH_LAST_STATE'])
        new_file.unlink()
        self.tick()
        self.assertFalse(self.n['ADDON_REFRESH_LAST_STATE'])
        self.assertEqual(self.n['_tag_view3d_redraw'].call_count, 2)

    def test_failed_watch_retries_and_preserves_error_for_explicit_review(self):
        original = self.n['_source_changed']
        self.n['_source_changed'] = Mock(side_effect=OSError('temporary file access'))
        self.assertEqual(self.tick(), 10.0)
        self.assertIn('temporary file access', self.n['ADDON_REFRESH_LAST_ERROR'])
        self.n['_source_changed'] = original
        (self.root / 'new_module.py').write_text('added = True\n', encoding='utf-8')
        self.tick()
        self.assertTrue(self.n['ADDON_REFRESH_LAST_STATE'])
        self.assertTrue(self.n['_refresh_ui_visible']())

    def test_unregister_removes_only_owned_timer_and_can_register_again(self):
        foreign = lambda: None
        self.timers.add(foreign)
        self.n['_register_source_watch']()
        self.n['_unregister_source_watch']()
        self.n['_unregister_source_watch']()
        self.assertEqual(self.timers, {foreign})
        self.assertTrue(self.n['_register_source_watch']())
        self.assertEqual(self.timers, {foreign, self.n['_source_watch_deferred']})

    def test_manual_refresh_reads_fresh_source_even_when_playing(self):
        self.n['bpy'].context.window_manager.windows = [NS(screen=NS(is_animation_playing=True))]
        self.n['ADDON_REFRESH_LAST_STATE'] = True
        (self.root / 'new_module.py').write_text('added = True\n', encoding='utf-8')
        operator = self.operator()
        self.assertEqual(operator.execute(None), {'FINISHED'})
        self.assertTrue(self.n['ADDON_REFRESH_PENDING'])
        self.assertIn(self.n['_reload_addon_deferred'], self.timers)

    def test_manual_refresh_validates_new_files_before_scheduling_reload(self):
        self.n['ADDON_REFRESH_LAST_STATE'] = True
        (self.root / 'broken.py').write_text('def invalid(\n', encoding='utf-8')
        self.assertEqual(self.operator().execute(None), {'CANCELLED'})
        self.assertFalse(self.n['ADDON_REFRESH_PENDING'])
        self.assertNotIn(self.n['_reload_addon_deferred'], self.timers)
        self.assertTrue(self.n['ADDON_REFRESH_LAST_ERROR'])

    def test_manual_refresh_rejects_stale_changed_flag_when_files_are_current(self):
        self.n['_register_source_watch']()
        self.n['ADDON_REFRESH_LAST_STATE'] = True
        probe = self.n['_source_changed'] = Mock(wraps=self.n['_source_changed'])
        self.assertEqual(self.operator().execute(None), {'CANCELLED'})
        probe.assert_called_once_with()
        self.assertFalse(self.n['ADDON_REFRESH_LAST_STATE'])
        self.assertNotIn(self.n['_reload_addon_deferred'], self.timers)

    def test_signature_still_reads_modified_and_deleted_source_without_positive_cache(self):
        before = self.source.stat()
        self.source.write_text('value = 2\n', encoding='utf-8')
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        self.assertTrue(self.n['_source_changed']())
        self.n['ADDON_LOADED_SIGNATURE'] = self.n['_source_signature']()
        self.source.unlink()
        self.assertTrue(self.n['_source_changed']())


if __name__ == '__main__':
    unittest.main()
