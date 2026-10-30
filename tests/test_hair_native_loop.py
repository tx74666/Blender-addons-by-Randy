"""Only an exact boundary of the same live native playback may reseed Hair."""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_wiggle2_adapter.py'


class NativeLoopTests(unittest.TestCase):
    def setUp(self):
        self.scene = NS(use_preview_range=False, frame_start=1, frame_end=6,
                        frame_preview_start=2, frame_preview_end=5, frame_step=1, frame_current=1)
        self.screen = NS(is_animation_playing=True)
        self.window = NS(scene=self.scene, screen=self.screen)
        self.context = NS(scene=self.scene, window=self.window, screen=self.screen,
            window_manager=NS(windows=[self.window]), view_layer=NS(update=Mock()),
            temp_override=lambda **kwargs: nullcontext())
        self.session = dict(scene=self.scene, lastframe=6, lease=object(), channels=object(),
            playback_window=self.window, playback_screen=self.screen,
            playback_bounds=(False, 1, 6, 1), physics_paused=False, started_playback=False)
        self.ns = dict(bpy=NS(context=self.context, ops=NS(screen=NS(animation_play=Mock()))),
            runtime=NS(reseed=Mock()), _SESSION=self.session, _STOPPING=False,
            _changed=Mock(return_value=''), stop_preview=Mock(),
            _fail=lambda message: (_ for _ in ()).throw(ValueError(message)))
        names = {'_playback_bounds', '_native_playback_wrap', '_start_playback', '_frame_guard'}
        nodes = [node for node in ast.parse(SOURCE.read_bytes()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
    def guard(self):
        self.ns['_frame_guard'](self.scene)
    def test_exact_native_end_start_reseeds_only_lease_and_retains_session(self):
        before_channels = self.session['channels']
        self.guard()
        self.context.view_layer.update.assert_called_once_with()
        self.ns['runtime'].reseed.assert_called_once_with(self.session['lease'], skip_frame=True)
        self.ns['stop_preview'].assert_not_called()
        self.assertIs(self.ns['_SESSION'], self.session)
        self.assertIs(self.session['channels'], before_channels)
        self.assertEqual(self.session['lastframe'], 1)
        self.assertEqual(self.session['native_wraps'], 1)
        self.assertEqual(self.scene.frame_current, 1)
    def test_forward_after_wrap_continues_without_second_seed(self):
        self.guard()
        self.scene.frame_current = 2
        self.guard()
        self.assertEqual(self.session['lastframe'], 2)
        self.assertEqual(self.ns['runtime'].reseed.call_count, 1)
        self.ns['stop_preview'].assert_not_called()
    def test_preview_range_is_the_effective_native_range(self):
        self.scene.use_preview_range = True
        self.scene.frame_current = 2
        self.session.update(lastframe=5, playback_bounds=(True, 2, 5, 1))
        self.guard()
        self.assertEqual(self.session['lastframe'], 2)
        self.ns['stop_preview'].assert_not_called()
    def test_boundary_while_stopped_remains_a_seek_refusal(self):
        self.screen.is_animation_playing = False
        self.guard()
        self.ns['stop_preview'].assert_called_once()
        self.ns['runtime'].reseed.assert_not_called()
    def test_nonboundary_backwards_seek_while_playing_is_refused(self):
        self.session['lastframe'] = 4
        self.guard()
        self.ns['stop_preview'].assert_called_once()
        self.ns['runtime'].reseed.assert_not_called()
    def test_positive_jump_beyond_existing_limit_is_not_relaxed(self):
        self.session['lastframe'], self.scene.frame_current = 1, 6
        self.guard()
        self.ns['stop_preview'].assert_called_once()
        self.ns['runtime'].reseed.assert_not_called()
    def test_changed_range_or_step_does_not_inherit_boundary_permission(self):
        for field, value in (('frame_end', 8), ('use_preview_range', True), ('frame_step', 2)):
            original = getattr(self.scene, field)
            setattr(self.scene, field, value)
            with self.subTest(field=field):
                self.assertFalse(self.ns['_native_playback_wrap'](self.session, self.scene, 1))
            setattr(self.scene, field, original)
    def test_replaced_or_closed_window_and_screen_are_refused(self):
        self.context.window_manager.windows = []
        self.assertFalse(self.ns['_native_playback_wrap'](self.session, self.scene, 1))
        self.context.window_manager.windows = [self.window]
        self.window.screen = NS(is_animation_playing=True, replacement=True)
        self.assertFalse(self.ns['_native_playback_wrap'](self.session, self.scene, 1))
    def test_owner_or_rest_failure_is_not_overridden_by_a_legal_boundary(self):
        self.ns['_changed'].return_value = 'Hair Rest or parent relationships changed.'
        self.guard()
        self.ns['runtime'].reseed.assert_not_called()
        self.ns['stop_preview'].assert_called_once_with(reason='Hair Rest or parent relationships changed.')
    def test_seed_failure_uses_existing_owned_cleanup(self):
        self.ns['runtime'].reseed.side_effect = ValueError('native seed failed')
        self.guard()
        self.ns['stop_preview'].assert_called_once()
        self.assertNotIn('native_wraps', self.session)
    def test_paused_component_keeps_its_pause_preference_through_boundary(self):
        self.session['physics_paused'] = True
        self.guard()
        self.assertTrue(self.session['physics_paused'])
        self.ns['stop_preview'].assert_not_called()
    def test_start_captures_an_already_playing_window_without_restarting_it(self):
        for field in ('playback_window', 'playback_screen', 'playback_bounds'):
            self.session.pop(field)
        self.ns['_start_playback'](self.context, self.session)
        self.assertIs(self.session['playback_window'], self.window)
        self.assertEqual(self.session['playback_bounds'], (False, 1, 6, 1))
        self.ns['bpy'].ops.screen.animation_play.assert_not_called()
    def test_explicit_start_captures_after_native_play_has_succeeded(self):
        self.screen.is_animation_playing = False
        def play():
            self.screen.is_animation_playing = True
            return {'FINISHED'}
        self.ns['bpy'].ops.screen.animation_play.side_effect = play
        self.ns['_start_playback'](self.context, self.session)
        self.assertTrue(self.session['started_playback'])
        self.assertIs(self.session['playback_screen'], self.screen)
    def test_missing_native_context_never_licenses_a_wrap(self):
        self.session.pop('playback_window')
        self.assertFalse(self.ns['_native_playback_wrap'](self.session, self.scene, 1))


if __name__ == '__main__':
    unittest.main()
