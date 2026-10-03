"""Native Bone Collection visibility never changes display or rig ownership."""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import bone_collections as groups, bone_display as display, bone_display_sync as sync
import test_limb_ik_blender as base
from test_bone_display_blender import fixture, views, content, sampled_poses, activate


def simple():
    base.ensure_registered()
    base.reset_scene()
    rig = base.make_humanoid('Native Collection Character')
    groups.simplify_body_collections(rig)
    activate(rig, 'POSE')
    sync.register()
    return rig


def eyes(rig):
    return groups.body_collection(rig), rig.data.collections_all['Original']


def ownership(rigs):
    return {rig.name: {
        'constraints': tuple((pb.name, tuple((con.name, con.mute, con.influence)
                             for con in pb.constraints)) for pb in rig.pose.bones),
        'locks': tuple((pb.name, tuple(pb.lock_location), tuple(pb.lock_rotation),
                       pb.lock_rotation_w, pb.lock_rotations_4d, tuple(pb.lock_scale))
                      for pb in rig.pose.bones),
        'drivers': tuple((curve.data_path, curve.array_index, curve.mute,
                          curve.driver.expression) for curve in
                         (rig.animation_data.drivers if rig.animation_data else ())),
        'view': display.view_mode(rig),
    } for rig in rigs}


def handlers():
    return tuple(handler for name in ('load_post', 'undo_post', 'redo_post')
                 for handler in getattr(bpy.app.handlers, name)
                 if getattr(handler, '__module__', '') == sync.__name__)


class NativeVisibilityTests(unittest.TestCase):
    def tearDown(self):
        sync.unregister()

    def assertPendingDoesNothing(self, rigs):
        before = views(rigs), content(rigs), ownership(rigs), sampled_poses(rigs)
        sync.sync_pending()
        for rig in rigs:
            sync.completed(rig)
            self.assertEqual(sync.last_error(rig), '')
        self.assertEqual(before, (views(rigs), content(rigs), ownership(rigs), sampled_poses(rigs)))

    def test_eye_and_solo_star_preserve_rigging_and_other_characters(self):
        main, dress, other, other_dress, *_ = fixture()
        rigs = (main, dress, other, other_dress)
        sync.register()
        protected, poses, owned = content(rigs), sampled_poses(rigs), ownership(rigs)
        other_views = views((dress, other, other_dress))
        body, original = eyes(main)
        initial_body = body.is_visible
        initial_shapes = main.data.show_bone_custom_shapes
        initial_style = main.data.display_type
        for visible in (True, False):
            original.is_visible = visible
            sync.sync_pending()
            self.assertEqual(body.is_visible, initial_body)
            self.assertEqual(original.is_visible, visible)
            self.assertIsNone(display.view_mode(main))
            self.assertEqual(main.data.show_bone_custom_shapes, initial_shapes)
            self.assertEqual(main.data.display_type, initial_style)
            self.assertEqual(other_views, views((dress, other, other_dress)))
        original.is_solo = True
        self.assertPendingDoesNothing(rigs)
        self.assertTrue(original.is_solo)
        self.assertFalse(body.is_solo)
        original.is_solo = False
        body.is_solo = True
        self.assertPendingDoesNothing(rigs)
        self.assertEqual(content(rigs), protected)
        self.assertEqual(sampled_poses(rigs), poses)
        self.assertEqual(ownership(rigs), owned)

    def test_bulk_visibility_and_edit_mode_remain_exact_artist_choices(self):
        main = simple()
        body, original = eyes(main)
        for mode in ('POSE', 'EDIT', 'OBJECT'):
            activate(main, mode)
            for visible in (True, False):
                body.is_visible = original.is_visible = visible
                self.assertPendingDoesNothing((main,))
                self.assertEqual((body.is_visible, original.is_visible), (visible, visible))
                self.assertIsNone(display.view_mode(main))
        activate(main, 'POSE')
        main.data.collections.active = original
        self.assertPendingDoesNothing((main,))

    def test_explicit_panel_views_still_work_without_native_feedback(self):
        main, dress, *_ = fixture()
        sync.register()
        for mode in ('ORIGINAL', 'HAIR', 'DRESS'):
            display.show_native(bpy.context, main, mode)
            self.assertEqual(display.view_mode(main), mode)
            self.assertPendingDoesNothing((main, dress))
            display.restore_view(dress)
            self.assertPendingDoesNothing((main, dress))
        display.show_controls(bpy.context, main, 'ALL')
        self.assertIsNone(display.view_mode(main))
        self.assertPendingDoesNothing((main, dress))

    def test_registration_and_reload_install_no_callbacks(self):
        main = simple()
        eyes(main)[1].is_visible = True
        before = views((main,))
        with patch.object(bpy.msgbus, 'subscribe_rna', side_effect=AssertionError('No native eye subscriptions')), \
             patch.object(bpy.app.timers, 'register', side_effect=AssertionError('No visibility timer')):
            sync.register()
            importlib.reload(sync)
            sync.register()
        self.assertEqual(handlers(), ())
        self.assertIsNone(sync._TIMER)
        self.assertPendingDoesNothing((main,))
        self.assertEqual(views((main,)), before)

    def test_upgrade_cleans_legacy_timer_message_owner_and_history_callbacks(self):
        main = simple()
        before = views((main,))
        legacy_state = {'__name__': sync.__name__, '_REGISTERED': True,
                        '_OWNER': object(), '_ENTRIES': {1: 'entry'}, '_PENDING': {1: 'ORIGINAL'},
                        '_ERRORS': {1: 'error'}, '_JOURNAL': [('old event',)]}
        exec('def _tick():\n    return 0.2\ndef _load_post(_unused):\n    pass\n'
             'def _undo_redo_post(_unused):\n    pass\n', legacy_state)
        tick = legacy_state['_TIMER'] = legacy_state['_tick']
        bpy.app.timers.register(tick, first_interval=60, persistent=True)
        sync._TIMER = tick  # In-place reload retains this reference too.
        bpy.app.handlers.load_post.append(legacy_state['_load_post'])
        bpy.app.handlers.undo_post.append(legacy_state['_undo_redo_post'])
        bpy.app.handlers.redo_post.append(legacy_state['_undo_redo_post'])
        try:
            with patch.object(bpy.msgbus, 'clear_by_owner', wraps=bpy.msgbus.clear_by_owner) as clear:
                importlib.reload(sync)
                sync.register()
                self.assertTrue(any(call.args == (legacy_state['_OWNER'],) for call in clear.call_args_list))
            self.assertFalse(bpy.app.timers.is_registered(tick))
            self.assertIsNone(sync._TIMER)
            self.assertEqual(handlers(), ())
            self.assertFalse(legacy_state['_REGISTERED'])
            self.assertEqual(legacy_state['_ENTRIES'], {})
            self.assertEqual(legacy_state['_PENDING'], {})
            self.assertEqual(legacy_state['_JOURNAL'], [])
            self.assertEqual(views((main,)), before)
        finally:
            if bpy.app.timers.is_registered(tick):
                bpy.app.timers.unregister(tick)
            sync.unregister()

    def test_save_reopen_and_undo_redo_preserve_only_native_flags(self):
        main = simple()
        name = main.name
        before = views((main,))
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message='Before native visibility')
        original = eyes(main)[1]
        original.is_visible = original.is_solo = True
        bpy.ops.ed.undo_push(message='Native visibility only')
        after = views((main,))
        self.assertPendingDoesNothing((main,))
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        main = bpy.data.objects[name]
        self.assertPendingDoesNothing((main,))
        self.assertEqual(views((main,)), before)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        main = bpy.data.objects[name]
        self.assertPendingDoesNothing((main,))
        self.assertEqual(views((main,)), after)
        protected = content((main,)), ownership((main,))
        with tempfile.TemporaryDirectory(prefix='cd-native-visibility-') as folder:
            path = str(Path(folder) / 'visibility.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            main = bpy.data.objects[name]
            self.assertPendingDoesNothing((main,))
            self.assertEqual(views((main,)), after)
            self.assertEqual((content((main,)), ownership((main,))), protected)
            self.assertEqual(handlers(), ())


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeVisibilityTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('BONE_DISPLAY_SYNC_PASSED', result.testsRun, flush=True)
