"""Native graph-dirtiness checks for read-only versus installed IK/FK drivers.

Run only in disposable Blender --background --factory-startup processes.
This is not a wall-clock benchmark or a claim about GUI click latency.
"""
import sys
import unittest
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import body_setup, limb_ik, limb_ik_fk as match
import test_body_calibration_blender as setup


class InstalledSwitchingGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Use an isolated background Blender for these fixtures.')
        character_designer.register()

    def make(self):
        rig, _skin = setup.fixture()
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        setup.prepare(rig)
        body_setup.generate(bpy.context, rig)
        match._update(bpy.context, rig)
        return rig, limb_ik._validate_inventory(rig)

    def graph_events(self, operation):
        # Settle prior fixture writes before observing the operation's native
        # graph effects. No timing thresholds or mocked Blender setters.
        bpy.context.view_layer.update()
        bpy.context.evaluated_depsgraph_get().update()
        events = []

        def observe(_scene, _graph):
            events.append(True)

        bpy.app.handlers.depsgraph_update_post.append(observe)
        try:
            result = operation()
            bpy.context.view_layer.update()
            return result, len(events)
        finally:
            bpy.app.handlers.depsgraph_update_post.remove(observe)

    def test_existing_switch_drivers_do_not_dirty_native_graph(self):
        rig, inventory = self.make()
        first = next(iter(inventory['rigs'].values()))
        target = rig.pose.bones[first['target'].name]
        target.location += Vector((.008, -.007, .005))
        match._update(bpy.context, rig)
        matrices = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
        channels = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
        paths = match.owned_driver_paths(rig)
        count, events = self.graph_events(lambda: match.ensure_switching(rig, inventory))
        self.assertEqual(count, 0)
        self.assertEqual(events, 0, 'Read-only ownership validation must not request a new graph solve')
        self.assertEqual(match.owned_driver_paths(rig), paths)
        self.assertEqual({pb.name: pb.matrix for pb in rig.pose.bones}, matrices)
        self.assertEqual({pb.name: pb.matrix_basis for pb in rig.pose.bones}, channels)
        limb_ik._validate_inventory(rig)

    def test_first_installation_still_requests_native_evaluation(self):
        rig, inventory = self.make()
        # Reproduce a supported Direct legacy graph without switch drivers.
        # This is an isolated fixture, never an edit of the artist rig.
        match.remove_switching(rig, inventory)
        match._update(bpy.context, rig)
        inventory = limb_ik._validate_inventory(rig)
        matrices = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
        count, events = self.graph_events(lambda: match.ensure_switching(rig, inventory))
        self.assertEqual(count, 4)
        self.assertGreaterEqual(events, 1, 'New influence drivers must be evaluated before matching')
        inventory = limb_ik._validate_inventory(rig)
        self.assertTrue(match.owned_driver_paths(rig))
        for item in inventory['rigs'].values():
            self.assertEqual(match.mode_for_rig(rig, item), 'IK')
        match._verify(rig, matrices)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(InstalledSwitchingGraphTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise RuntimeError('Installed IK/FK graph checks failed.')
print('LIMB_IK_FK_PERFORMANCE_PASSED', result.testsRun)
