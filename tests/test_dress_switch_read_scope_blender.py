"""Operation-local Dress proofs and fresh validation after native callbacks.

Run in an isolated Blender. The fixtures create their own character and meshes;
the suite never opens or saves the artist scene.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (
    body_original_mode as original, bone_display as display,
    skirt_original_mode as dress, skirt_rig as skirt,
)
import test_skirt_original_mode_blender as fixture_tools


class DressReadScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        self.source, self.rig, self.main, self.foreign, self.record = fixture_tools.fixture()

    def test_capture_and_checkpoint_share_one_consecutive_read_proof(self):
        entries = dress.prepare(bpy.context, self.main)
        expected_pose = dress.capture(bpy.context, self.main, entries)
        expected_checkpoint = dress.checkpoint(bpy.context, self.main, entries)
        with patch.object(dress, '_resolve', wraps=dress._resolve) as resolve:
            targets = dress._resolve(bpy.context, self.main, entries)
            actual_pose = dress.capture(bpy.context, self.main, entries, targets=targets)
            actual_checkpoint = dress.checkpoint(bpy.context, self.main, entries, targets=targets)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(actual_pose, expected_pose)
        self.assertEqual(actual_checkpoint, expected_checkpoint)

    def test_preserve_reads_once_without_an_update_when_pose_already_matches(self):
        entries = dress.prepare(bpy.context, self.main)
        wanted = dress.capture(bpy.context, self.main, entries)
        with (patch.object(dress, '_resolve', wraps=dress._resolve) as resolve,
              patch.object(dress, '_update', wraps=dress._update) as update):
            dress._preserve(bpy.context, self.main, entries, wanted)
        self.assertEqual(resolve.call_count, 1)
        update.assert_not_called()

    def test_native_update_callback_invalidates_preserve_source_link_proof(self):
        entries = dress.prepare(bpy.context, self.main)
        checkpoint = dress.checkpoint(bpy.context, self.main, entries)
        wanted = dress.capture(bpy.context, self.main, entries)
        owner = entries[0]['owner']
        name = self.record['chains'][0]['def'][1]
        wanted[owner][name].translation.x += .003
        saved_rig = self.source[skirt.RIG_KEY]
        native_update = dress._update

        def change_source_link_after_update(context, targets):
            native_update(context, targets)
            # Shared-source discovery and exact inventory resolution both
            # authorize this persistent Object relationship, not the source's
            # redundant display ownership tag.
            self.source[skirt.RIG_KEY] = self.foreign

        try:
            with (patch.object(dress, '_update', side_effect=change_source_link_after_update) as update,
                  patch.object(dress, '_resolve', wraps=dress._resolve) as resolve,
                  patch.object(dress, 'capture', wraps=dress.capture) as capture):
                with self.assertRaisesRegex(ValueError, '(?i)ownership|removed|reassigned|source|inventory'):
                    dress._preserve(bpy.context, self.main, entries, wanted)
            self.assertEqual(update.call_count, 1)
            self.assertEqual(resolve.call_count, 2)
            self.assertEqual(capture.call_count, 1)
        finally:
            # The test callback is external to the transfer. Restore its link
            # edit before restoring the private preservation checkpoint.
            self.source[skirt.RIG_KEY] = saved_rig
            dress.rollback(bpy.context, checkpoint)

    def test_body_return_reuses_only_capture_checkpoint_then_keeps_fresh_verify(self):
        original.enter(bpy.context, self.main)
        seen = []
        native_capture, native_checkpoint = dress.capture, dress.checkpoint

        def capture(context, main, entries, **kwargs):
            if kwargs.get('targets') is not None:
                seen.append(('capture', kwargs['targets']))
            return native_capture(context, main, entries, **kwargs)

        def checkpoint(context, main, entries, **kwargs):
            seen.append(('checkpoint', kwargs.get('targets')))
            return native_checkpoint(context, main, entries, **kwargs)

        with (patch.object(dress, 'capture', side_effect=capture),
              patch.object(dress, 'checkpoint', side_effect=checkpoint),
              patch.object(dress, 'verify', wraps=dress.verify) as verify):
            original.leave(bpy.context, self.main)
        self.assertEqual([item[0] for item in seen[:2]], ['capture', 'checkpoint'])
        self.assertIsNotNone(seen[0][1])
        self.assertIs(seen[0][1], seen[1][1])
        # Verification after Dress transfer and after final Forearm refresh
        # retains its independent ownership/constraint resolution.
        self.assertEqual(verify.call_count, 2)
        self.assertFalse(original.active(self.main))

    def test_post_refresh_constraint_edit_is_rejected_and_authored_state_rolls_back(self):
        original.enter(bpy.context, self.main)
        name = self.record['chains'][0]['def'][1]
        fixture_tools.direct_rotation(self.rig, name, .12)
        session = self.main[original.SESSION]
        references = dict(self.main.get(original.DISPLAY_REFS, {}))
        correction = self.source.get(dress.CORRECTIONS)
        channels = fixture_tools.pose_channels(self.rig)
        constraints = fixture_tools.constraints_state(self.rig)
        view = display._snapshot(self.main)
        protected = fixture_tools.protected_state(self.main, self.foreign)
        wanted = fixture_tools.world_pose(self.rig, fixture_tools.native_names(self.record))
        vertices = fixture_tools.world_vertices(self.source)
        copy = self.rig.pose.bones[name].constraints['Skirt manual pose']
        phases = []

        def late_refresh():
            phases.append('refresh')
            if len(phases) == 1:
                self.assertFalse(original.active(self.main))
                copy.mix_mode = 'AFTER_FULL'

        with patch.object(dress, 'verify', wraps=dress.verify) as verify:
            with self.assertRaisesRegex(ValueError, '(?i)edited|extra pose constraint'):
                original._leave(bpy.context, self.main, late_refresh)
        self.assertEqual(len(phases), 2)
        self.assertEqual(verify.call_count, 2)
        self.assertTrue(original.active(self.main))
        self.assertEqual(self.main[original.SESSION], session)
        self.assertEqual(dict(self.main.get(original.DISPLAY_REFS, {})), references)
        self.assertEqual(self.source.get(dress.CORRECTIONS), correction)
        self.assertEqual(fixture_tools.pose_channels(self.rig), channels)
        self.assertEqual(fixture_tools.constraints_state(self.rig), constraints)
        self.assertEqual(display._snapshot(self.main), view)
        self.assertEqual(fixture_tools.protected_state(self.main, self.foreign), protected)
        actual = fixture_tools.world_pose(self.rig, wanted)
        self.assertLessEqual(max(dress._difference(matrix, actual[name])
                                 for name, matrix in wanted.items()), fixture_tools.POSE_LIMIT)
        self.assertLessEqual(fixture_tools.geometry_error(vertices, fixture_tools.world_vertices(self.source)),
                             fixture_tools.GEOMETRY_LIMIT)
        # The restored session is still usable; authored Dress edits survive.
        original.leave(bpy.context, self.main)
        self.assertFalse(original.active(self.main))
        self.assertLessEqual(fixture_tools.geometry_error(vertices, fixture_tools.world_vertices(self.source)),
                             fixture_tools.GEOMETRY_LIMIT)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DressReadScopeTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f'PASS Dress read scope {result.testsRun} tests', flush=True)
