"""RGC native display, legacy migration, artist preservation, and real Undo."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import body_setup, bone_collections as groups
import test_body_setup_plan_blender as fixture


def stable_state(rig, body):
    return {
        'rest': {b.name: (b.parent.name if b.parent else None,
                         tuple(round(v, 6) for row in b.matrix_local for v in row),
                         round(b.length, 6)) for b in rig.data.bones},
        'basis': {p.name: tuple(round(v, 6) for row in p.matrix_basis for v in row)
                  for p in rig.pose.bones},
        'weights': [tuple((g.group, g.weight) for g in v.groups) for v in body.data.vertices],
        'constraints': {p.name: tuple(p.constraints.keys()) for p in rig.pose.bones},
        'shapes': {p.name: p.custom_shape.name if p.custom_shape else None for p in rig.pose.bones},
    }


def stale_layout():
    rig, body = fixture.fixture()
    groups.simplify_body_collections(rig)
    rig.data.bones['shin.L'].hide = True
    if hasattr(rig.pose.bones['shin.L'], 'hide'):
        rig.pose.bones['shin.L'].hide = True
    return rig, body


class CD_TEST_OT_native_display(bpy.types.Operator):
    bl_idname = 'cd_test.native_display'
    bl_label = 'Test Native Display'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        body_setup.restore_original_display(context, context.object)
        return {'FINISHED'}


class OriginalDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()
        bpy.utils.register_class(CD_TEST_OT_native_display)

    def assert_original(self, rig):
        self.assertTrue(rig.data[groups.NATIVE_ONLY_KEY])
        self.assertIsNone(groups.body_collection(rig))
        original = rig.data.collections_all['Original']
        self.assertEqual(rig.data.collections.active, original)
        self.assertTrue(original.is_visible_effectively)
        self.assertEqual(set(original.bones.keys()), set(rig.data.bones.keys()))
        self.assertTrue(all(not b.hide for b in rig.data.bones))
        self.assertTrue(all(not p.hide for p in rig.pose.bones if hasattr(p, 'hide')))

    def assert_same_character(self, rig, body, saved):
        current = stable_state(rig, body)
        for name, matrix in current['basis'].items():
            self.assertLess(max(abs(a-b) for a, b in zip(matrix, saved['basis'][name])), 2e-4)
        self.assertEqual({k: v for k, v in current.items() if k != 'basis'},
                         {k: v for k, v in saved.items() if k != 'basis'})

    def test_complete_remove_and_generate_again(self):
        rig, body = fixture.fixture()
        shape = bpy.data.objects.new('Artist Head Display', bpy.data.meshes.new('Artist Shape'))
        rig.pose.bones['Head'].custom_shape = shape
        saved = stable_state(rig, body)
        body_setup.generate(bpy.context, rig)
        self.assertTrue(body_setup.has_generated(rig))
        body_setup.remove(bpy.context, rig)
        self.assert_original(rig)
        self.assert_same_character(rig, body, saved)
        body_setup.generate(bpy.context, rig)
        self.assertTrue(body_setup.has_generated(rig))
        self.assertTrue(groups.body_collection(rig).is_visible_effectively)
        self.assertFalse(rig.data.collections_all['Original'].is_visible)
        self.assertNotIn(groups.NATIVE_ONLY_KEY, rig.data)
        body_setup.remove(bpy.context, rig)
        self.assert_original(rig)
        self.assert_same_character(rig, body, saved)

    def test_legacy_idempotent_and_save_reopen(self):
        rig, body = stale_layout()
        saved = stable_state(rig, body)
        first = body_setup.restore_original_display(bpy.context, rig)
        self.assertTrue(first['changed'])
        self.assert_original(rig)
        self.assertEqual(stable_state(rig, body), saved)
        self.assertFalse(groups.show_original_after_removal(rig)['changed'])
        rig_name, body_name = rig.name, body.name
        with tempfile.TemporaryDirectory(prefix='cd-native-display-') as folder:
            path = str(Path(folder) / 'native.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            rig, body = bpy.data.objects[rig_name], bpy.data.objects[body_name]
            self.assert_original(rig)
            self.assertEqual(stable_state(rig, body), saved)
            self.assertEqual(groups.migrate_removed_body_layouts(bpy.context.scene)['repaired'], [])

    def test_artist_hierarchy_and_internal_other_preserved(self):
        rig, body = stale_layout()
        parent = groups.body_collection(rig)
        artist = rig.data.collections.new('Artist Picks', parent=parent)
        artist.assign(rig.data.bones['Head'])
        artist.is_visible, artist.is_solo = False, True
        other = rig.data.collections.new('_Other', parent=parent)
        other[groups.GROUP_KEY] = groups.OTHER_NAME
        other.is_visible = False
        saved = stable_state(rig, body)
        groups.show_original_after_removal(rig)
        self.assertEqual(artist.parent, parent)
        self.assertEqual(set(artist.bones.keys()), {'Head'})
        self.assertFalse(artist.is_visible)
        self.assertTrue(artist.is_solo)
        self.assertEqual(other.parent, parent)
        self.assertFalse(other.is_visible)
        self.assertTrue(rig.data.collections_all['Original'].is_visible_effectively)
        self.assertEqual(stable_state(rig, body), saved)

    def test_migration_targets_current_character_only(self):
        rig, _body = stale_layout()
        backup = rig.copy()
        backup.data = rig.data.copy()
        bpy.context.scene.collection.objects.link(backup)
        backup.name = 'Backup rig'
        unowned = bpy.data.objects.new('Artist unowned rig', bpy.data.armatures.new('Artist rig'))
        bpy.context.scene.collection.objects.link(unowned)
        unowned.data.collections.new('Body')
        unowned.data.collections.new('Original')
        before_backup = groups.snapshot_layout(backup)
        before_artist = groups.snapshot_layout(unowned)
        timer = groups._MIGRATION_TIMER
        if timer is not None and bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
        groups._native_migration_once()
        self.assert_original(rig)
        self.assertEqual(groups.snapshot_layout(backup), before_backup)
        self.assertEqual(groups.snapshot_layout(unowned), before_artist)
        rig.data.collections_all['Original'].is_visible = False
        groups._native_migration_once()
        self.assertFalse(rig.data.collections_all['Original'].is_visible)
        # Load only schedules a single task; unregister must remove it cleanly.
        groups._load_native_migration(None)
        self.assertTrue(bpy.app.timers.is_registered(groups._MIGRATION_TIMER))
        pending = groups._MIGRATION_TIMER
        groups.unregister_handlers()
        self.assertFalse(bpy.app.timers.is_registered(pending))
        groups.register_handlers()

    def test_failure_rolls_back_display(self):
        rig, body = stale_layout()
        before = groups.snapshot_layout(rig)
        saved = stable_state(rig, body)
        with patch.object(groups, '_save_backup', side_effect=RuntimeError('Injected backup failure')):
            with self.assertRaisesRegex(RuntimeError, 'Injected backup failure'):
                groups.show_original_after_removal(rig)
        self.assertEqual(groups.snapshot_layout(rig), before)
        self.assertEqual(stable_state(rig, body), saved)

    def test_real_undo_and_redo(self):
        rig, _body = stale_layout()
        bpy.ops.object.mode_set(mode='OBJECT')
        rig_name = rig.name
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message='Before native display')
        self.assertEqual(bpy.ops.cd_test.native_display(), {'FINISHED'})
        bpy.ops.ed.undo_push(message='After native display')
        self.assert_original(rig)
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        rig = bpy.data.objects[rig_name]
        self.assertIsNotNone(groups.body_collection(rig))
        self.assertNotIn(groups.NATIVE_ONLY_KEY, rig.data)
        self.assertTrue(rig.data.bones['shin.L'].hide)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        self.assert_original(bpy.data.objects[rig_name])


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OriginalDisplayTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
