"""Native Bone Collection eye changes use the same reversible display service."""
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import bone_collections as groups, bone_display as display, bone_display_sync as sync
import test_limb_ik_blender as base
from test_bone_display_blender import fixture, views, content, sampled_poses, visible, activate


def simple():
    base.ensure_registered()
    base.reset_scene()
    rig = base.make_humanoid('Collection Eye Character')
    groups.simplify_body_collections(rig)
    activate(rig, 'POSE')
    sync.register()
    return rig


def eyes(rig):
    return groups.body_collection(rig), rig.data.collections_all['Original']


class BoneDisplaySyncTests(unittest.TestCase):
    def tearDown(self):
        sync.unregister()

    def test_original_eye_fallback_is_reversible_and_preserves_other_rigs(self):
        main, dress, other, other_dress, *_ = fixture()
        rigs = (main, dress, other, other_dress)
        sync.register()
        body, original = eyes(main)
        before = views(rigs)
        protected, poses = content(rigs), sampled_poses(rigs)
        original.is_visible = True
        sync.sync_pending()  # Timer fallback, without a message-bus callback.
        self.assertEqual(display.view_mode(main), 'ORIGINAL')
        self.assertFalse(body.is_visible)
        self.assertTrue(original.is_visible)
        self.assertFalse(main.data.collections_all['_Internal'].is_visible)
        self.assertFalse(main.data.show_bone_custom_shapes)
        self.assertEqual(main.data.display_type, 'OCTAHEDRAL')
        self.assertEqual(visible(main), set(original.bones.keys()))
        self.assertEqual(views((other, other_dress)), {r.name: before[r.name] for r in (other, other_dress)})
        self.assertEqual(content(rigs), protected)
        self.assertEqual(sampled_poses(rigs), poses)
        with self.assertRaisesRegex(ValueError, 'Restore the temporary'):
            groups.simplify_body_collections(main)
        original.is_visible = False
        sync.sync_pending()
        self.assertIsNone(display.view_mode(main))
        self.assertFalse(original.is_visible)
        self.assertEqual(views(rigs), before)
        self.assertEqual(content(rigs), protected)
        self.assertEqual(sampled_poses(rigs), poses)

    def test_body_eye_returns_controls_and_last_explicit_event_wins(self):
        main = simple()
        body, original = eyes(main)
        key = main.as_pointer()
        original.is_visible = True
        sync._notice(key, 'ORIGINAL')
        sync.sync_pending()
        body.is_visible = True
        sync._notice(key, 'BODY')
        sync.sync_pending()
        self.assertIsNone(display.view_mode(main))
        self.assertTrue(main.data.show_bone_custom_shapes)
        self.assertTrue(body.is_visible)
        self.assertFalse(original.is_visible)
        for last in ('BODY', 'ORIGINAL'):
            display.show_controls(bpy.context, main, 'BODY')
            body.is_visible = False
            sync.sync_pending()
            for role in (('ORIGINAL', 'BODY') if last == 'BODY' else ('BODY', 'ORIGINAL')):
                (body if role == 'BODY' else original).is_visible = True
                sync._notice(key, role)
            sync.sync_pending()
            self.assertEqual(display.view_mode(main), 'ORIGINAL' if last == 'ORIGINAL' else None)
            self.assertEqual((body.is_visible, original.is_visible), (last == 'BODY', last == 'ORIGINAL'))

    def test_panel_transitions_reseed_without_feedback_or_active_row_triggers(self):
        main, dress, *_ = fixture()
        sync.register()
        for mode in ('ORIGINAL', 'HAIR', 'DRESS'):
            display.show_native(bpy.context, main, mode)
            before = views((main, dress))
            sync.sync_pending()
            self.assertEqual(display.view_mode(main), mode)
            self.assertEqual(views((main, dress)), before)
            display.restore_view(dress)
            before = views((main, dress))
            sync.sync_pending()
            self.assertEqual(views((main, dress)), before)
        display.show_controls(bpy.context, main, 'ALL')
        before = views((main, dress))
        main.data.collections.active = main.data.collections_all['Original']
        sync.sync_pending()
        self.assertIsNone(display.view_mode(main))
        self.assertEqual(views((main, dress)), before)

    def test_registration_reload_undo_seed_and_save_reopen_never_switch_initial_state(self):
        main = simple()
        body, original = eyes(main)
        original.is_visible = True
        before = views((main,))
        # Loading/enabling with both eye flags set does not invent a user event.
        sync.register()
        sync.sync_pending()
        self.assertEqual(views((main,)), before)
        self.assertIsNone(display.view_mode(main))
        old_tick = sync._TIMER
        importlib.reload(sync)
        sync.register()
        self.assertFalse(bpy.app.timers.is_registered(old_tick))
        self.assertEqual(sum(getattr(h, '__module__', '') == sync.__name__ for h in bpy.app.handlers.undo_post), 1)
        original.is_visible = False
        sync._reset(None)  # Undo/redo/load callbacks seed, never replay.
        sync.sync_pending()
        self.assertIsNone(display.view_mode(main))
        original.is_visible = True
        sync.sync_pending()
        self.assertEqual(display.view_mode(main), 'ORIGINAL')
        protected, before = content((main,)), views((main,))
        name = main.name
        with tempfile.TemporaryDirectory(prefix='cd-display-sync-') as folder:
            path = str(Path(folder) / 'original.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            main = bpy.data.objects[name]
            self.assertEqual(sync._JOURNAL, [], 'Loading never replays a previous session eye event')
            sync.sync_pending()
            self.assertEqual(views((main,)), before)
            self.assertEqual(content((main,)), protected)
            eyes(main)[1].is_visible = False
            sync.sync_pending()
            self.assertIsNone(display.view_mode(main))
            self.assertTrue(main.data.show_bone_custom_shapes)

    def test_unowned_shared_and_edit_mode_are_not_changed(self):
        main = simple()
        body, original = eyes(main)
        original.pop(groups.GROUP_KEY)
        original.is_visible = True
        sync.sync_pending()
        self.assertIsNone(display.view_mode(main))
        original[groups.GROUP_KEY] = 'Original'
        original.is_visible = False
        sync._reset(None)
        duplicate = bpy.data.objects.new('Shared Readonly Rig', main.data)
        bpy.context.scene.collection.objects.link(duplicate)
        original.is_visible = True
        sync.sync_pending()
        self.assertIsNone(display.view_mode(main))
        bpy.data.objects.remove(duplicate, do_unlink=True)
        original.is_visible = False
        sync._reset(None)
        activate(main, 'EDIT')
        original.is_visible = True
        sync.sync_pending()
        self.assertIsNone(display.view_mode(main))
        activate(main, 'POSE')
        sync.sync_pending()
        self.assertIsNone(display.view_mode(main))

    def test_undo_redo_completes_native_click_snapshot_before_deferred_sync(self):
        main = simple()
        name = main.name
        before = views((main,))
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message='Before original eye')
        eyes(main)[1].is_visible = True
        bpy.ops.ed.undo_push(message='Native original eye before deferred callback')
        sync.sync_pending()
        after = views((main,))
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        main = bpy.data.objects[name]
        sync.sync_pending()
        self.assertEqual(views((main,)), before)
        self.assertIsNone(display.view_mode(main))
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        main = bpy.data.objects[name]
        sync.sync_pending()
        self.assertEqual(views((main,)), after)
        self.assertEqual(display.view_mode(main), 'ORIGINAL')
        # A second native click creates another pre-callback undo step. Undo
        # must also complete the earlier raw eye step, not just Redo.
        eyes(main)[0].is_visible = True
        bpy.ops.ed.undo_push(message='Native body eye before deferred callback')
        sync.sync_pending()
        controls = views((main,))
        self.assertIsNone(display.view_mode(main))
        for _ in range(2):
            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            main = bpy.data.objects[name]
            sync.sync_pending()
            self.assertEqual(views((main,)), after)
            self.assertEqual(display.view_mode(main), 'ORIGINAL')
            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            main = bpy.data.objects[name]
            sync.sync_pending()
            self.assertEqual(views((main,)), controls)
            self.assertIsNone(display.view_mode(main))

    def test_failed_switch_rolls_back_and_is_not_retried(self):
        main = simple()
        original = eyes(main)[1]
        before = views((main,))
        show_native, calls = display.show_native, []
        def fail(*args):
            calls.append(args)
            main.data.show_bone_custom_shapes = False
            raise ValueError('Injected eye switch failure')
        display.show_native = fail
        try:
            original.is_visible = True
            sync.sync_pending()
            self.assertEqual(views((main,)), before)
            self.assertIn('Injected eye switch failure', sync.last_error(main))
            sync.sync_pending()
            self.assertEqual(len(calls), 1)
        finally:
            display.show_native = show_native
        original.is_visible = True
        sync.sync_pending()
        self.assertEqual(sync.last_error(main), '')
        self.assertEqual(display.view_mode(main), 'ORIGINAL')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BoneDisplaySyncTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('BONE_DISPLAY_SYNC_PASSED', result.testsRun, flush=True)
