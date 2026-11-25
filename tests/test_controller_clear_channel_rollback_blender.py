"""Run in Blender to verify native F-Curve rollback for a mixed control clear."""
import importlib
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

import bpy


package = ModuleType('_test_clear')
package.__path__ = [str(Path(os.environ.get('CD_WRIST_OWNER_TEST_SOURCE',
    Path(__file__).resolve().parents[1] / 'addons/character_designer')))]
sys.modules[package.__name__] = package
clear = importlib.import_module(package.__name__ + '.controller_transform_clear')
keying = importlib.import_module(package.__name__ + '.limb_ik_wrist_keying')


class ChannelRollback(unittest.TestCase):
    frame = 25.1

    def setUp(self):
        self.action = bpy.data.actions.new('Rollback test')
        strip = self.action.layers.new('Keys').strips.new(type='KEYFRAME')
        self.slot = self.action.slots.new('OBJECT', 'Working')
        self.bag = strip.channelbags.new(self.slot)
        self.other = strip.channelbags.new(self.action.slots.new('OBJECT', 'Other'))

    def tearDown(self):
        bpy.data.actions.remove(self.action)

    def curve(self, path, index=0, *, bag=None, frames=()):
        curve = (bag or self.bag).fcurves.new(path, index=index)
        for frame in frames:
            point = curve.keyframe_points.insert(frame, frame + index)
            point.interpolation = 'BEZIER'
        return curve

    def journal(self):
        return (self.bag, lambda path: True,
                {(c.data_path, c.array_index): keying._point_snapshot(c, [0]) for c in self.bag.fcurves},
                {g.as_pointer() for g in self.bag.groups}, self.frame)

    def test_new_and_existing_scalar_and_array_channels_restore_exactly(self):
        scalar = self.curve('["uniform_scale"]', frames=(1, 35))
        vector_x = self.curve('location', frames=(12, self.frame, 41))
        vector_z = self.curve('location', 2, frames=(1, 57))
        empty = self.curve('["existing_empty"]')
        one = self.curve('["one_component_array"]', frames=(3,))
        foreign = self.curve('["uniform_scale"]', bag=self.other, frames=(self.frame,))
        empty_group = self.bag.groups.new('Existing empty')
        pointer = empty_group.as_pointer()
        journal = self.journal()
        other_snapshot = keying._point_snapshot(foreign, [0])
        for curve in (scalar, vector_x, empty, one):
            curve.keyframe_points.insert(self.frame, -19)
        self.curve('location', 1, frames=(self.frame,))
        self.curve('["new_scalar"]', frames=(self.frame,))
        self.curve('["new_array"]', frames=(self.frame,))
        self.bag.groups.new('Temporary group')

        clear._restore_keys(None, journal)
        restored = {(c.data_path, c.array_index): c for c in self.bag.fcurves}
        self.assertEqual(restored.keys(), journal[2].keys())
        for channel, saved in journal[2].items():
            self.assertTrue(keying._matches(saved, restored[channel]), channel)
        self.assertEqual(vector_z.keyframe_points[0].co.x, 1.)
        self.assertTrue(keying._matches(other_snapshot, foreign))
        self.assertEqual([g.as_pointer() for g in self.bag.groups], [pointer])
        clear._restore_keys(None, journal)  # A second rollback has no further work.

    def test_autokeyed_rna_scalar_one_element_array_and_vector_components(self):
        rig = bpy.data.objects.new('Native channels', None)
        try:
            rig.animation_data_create()
            rig.animation_data.action = self.action
            rig.animation_data.action_slot = self.slot
            rig['scalar'] = .5
            rig['single'] = [.5]
            self.assertTrue(rig.keyframe_insert(data_path='location', index=1, frame=5))
            self.assertTrue(rig.keyframe_insert(data_path='["scalar"]', frame=5))
            journal = self.journal()
            self.assertTrue(rig.keyframe_insert(data_path='["scalar"]', frame=self.frame))
            self.assertTrue(rig.keyframe_insert(data_path='["single"]', index=0, frame=self.frame))
            self.assertTrue(rig.keyframe_insert(data_path='location', frame=self.frame))
            clear._restore_keys(rig, journal)
            actual = {(c.data_path, c.array_index): c for c in self.bag.fcurves}
            self.assertEqual(actual.keys(), journal[2].keys())
            for channel, saved in journal[2].items():
                self.assertTrue(keying._matches(saved, actual[channel]), channel)
        finally:
            bpy.data.objects.remove(rig)

    def test_new_channel_at_another_frame_is_retained(self):
        journal = self.journal()
        curve = self.curve('["uniform_scale"]', frames=(47,))
        with self.assertRaisesRegex(RuntimeError, 'needs recovery'):
            clear._restore_keys(None, journal)
        self.assertEqual(curve.keyframe_points[0].co.x, 47.)

    def test_existing_empty_group_keeps_its_identity_on_commit_and_rollback(self):
        rig = bpy.data.objects.new('Empty group test', None)
        try:
            rig.animation_data_create()
            rig.animation_data.action = self.action
            rig.animation_data.action_slot = self.slot
            ctx = SimpleNamespace(preferences=SimpleNamespace(
                edit=SimpleNamespace(use_keyframe_insert_available=False)))
            for rollback in (True, False):
                name = 'Selected bone ' + str(rollback)
                original = self.bag.groups.new(name)
                original.color_set = 'THEME03'
                pointer = original.as_pointer()
                journal = self.journal()
                def run():
                    with clear._preserve_empty_groups(ctx, journal, [SimpleNamespace(name=name)]):
                        rig.keyframe_insert(data_path='rotation_euler', frame=self.frame, group=name)
                        if rollback:
                            try:
                                raise RuntimeError('Injected failure after native keying')
                            except RuntimeError:
                                clear._restore_keys(rig, journal)
                                raise
                if rollback:
                    with self.assertRaisesRegex(RuntimeError, 'Injected failure'):
                        run()
                else:
                    run()
                self.assertEqual(self.bag.groups.get(name).as_pointer(), pointer)
                self.assertEqual(original.color_set, 'THEME03')
                self.assertEqual(len(original.channels), 0 if rollback else 3)
                self.assertFalse(any(g.name.startswith('.Clear rotations ') for g in self.bag.groups))
        finally:
            bpy.data.objects.remove(rig)

    def test_an_unrelated_new_point_does_not_delete_the_authored_key_at_the_current_frame(self):
        curve = self.curve('location', frames=(self.frame,))
        journal = self.journal()
        curve.keyframe_points.insert(47, 1)
        with self.assertRaisesRegex(RuntimeError, 'needs recovery'):
            clear._restore_keys(None, journal)
        self.assertEqual(len(curve.keyframe_points), 2)


if __name__ == '__main__':
    results = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ChannelRollback))
    if not results.wasSuccessful():
        raise RuntimeError('Native channel rollback checks failed.')
