"""Two late-graph failures for the current-controls Rest acceptance transaction.

Factory/background only, through the trusted frozen-candidate runner. Reuse the
8183d4fc native fixture and witnesses without changing that pinned test. Each
wrapper calls the real regional operation before injecting its exception; no
ownership, inventory, Rest, surface, or rollback guard is mocked.
"""
from pathlib import Path
import copy
import hashlib
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import test_current_leg_rest_adoption_blender as base
from character_designer import (
    body_region as region, body_rest_adoption as adoption,
    foot_controls, limb_ik, control_pose_assets as poses,
)

BASE_SHA256 = '8183d4fc87e222be3f74586288d40e321f6db0c7e259e81d1030e551aea9de31'


class InjectedLateGraphFailure(RuntimeError):
    pass


class CurrentLegRestAdoptionLateFaultTests(unittest.TestCase):
    # Refer to the module, rather than importing/inheriting its TestCase, so
    # ordinary discovery in this derivative finds exactly these two tests.
    make = base.CurrentLegRestAdoptionTests.make
    rejection_snapshot = base.CurrentLegRestAdoptionTests.rejection_snapshot
    assert_old_restore_refused = base.CurrentLegRestAdoptionTests.assert_old_restore_refused
    tearDown = base.CurrentLegRestAdoptionTests.tearDown

    @classmethod
    def setUpClass(cls):
        fixture = Path(base.__file__).resolve()
        if hashlib.sha256(fixture.read_bytes()).hexdigest() != BASE_SHA256:
            raise RuntimeError('The independently pinned native fixture changed.')
        base.CurrentLegRestAdoptionTests.setUpClass.__func__(cls)

    def exercise_fault(self, stage):
        rig, mesh, action, motion, historical_rest = self.make()
        before = self.rejection_snapshot(rig, mesh, action, motion)
        current_rest = copy.deepcopy(poses.native_rest(rig))
        self.assertNotEqual(current_rest, historical_rest)
        reached = []
        original_operation = getattr(region, stage)

        def completed_operation_then_fail(context, actual, region_name, *args, **kwargs):
            # This is an exception after actual teardown/recreation, not a
            # pre-operation stub that could pass without testing graph rollback.
            result = original_operation(context, actual, region_name, *args, **kwargs)
            self.assertIs(actual, rig)
            self.assertEqual(region_name, 'LEGS')
            self.assertNotEqual(base.metadata(rig), before['metadata'])
            self.assertTrue(adoption.has_revision(rig))
            self.assertIsNone(adoption.checkpoint_rest_view(rig))
            inventory = limb_ik._validate_inventory(rig)
            if stage == 'release':
                self.assertEqual(region.state(rig, 'LEGS')['status'], 'RELEASED')
                self.assertTrue(result['released'])
                self.assertFalse(any(key[0] == 'LEG' for key in inventory['rigs']))
                self.assertFalse(foot_controls.records(rig))
                self.assertFalse(any(
                    bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                    and bone.get(limb_ik.KIND_KEY) == 'LEG'
                    for bone in rig.data.bones))
            else:
                self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')
                self.assertTrue(result['bound'])
                self.assertTrue({('LEG', 'L'), ('LEG', 'R')} <= set(inventory['rigs']))
                self.assertEqual(set(foot_controls.records(rig)), {'L', 'R'})
            self.assertEqual(poses.native_rest(rig), current_rest)
            self.assertEqual(base.body_state(mesh), before['body'])
            self.assertEqual(base.history(rig, action, motion), before['history'])
            reached.append(stage)
            raise InjectedLateGraphFailure('after actual regional ' + stage)

        with patch.object(region, stage, side_effect=completed_operation_then_fail):
            with self.assertRaisesRegex(InjectedLateGraphFailure, 'after actual regional ' + stage):
                adoption.accept_current_legs(bpy.context, rig)

        self.assertEqual(reached, [stage])
        # Includes complete current Rest, old Direct/owner/audit metadata,
        # source paint/Keys, Action recovery proofs, resources and native IDs.
        self.assertEqual(self.rejection_snapshot(rig, mesh, action, motion), before)
        self.assertEqual(poses.native_rest(rig), current_rest)
        self.assertNotEqual(poses.native_rest(rig), historical_rest)
        self.assertFalse(adoption.has_revision(rig))
        self.assertIsNone(adoption.checkpoint_rest_view(rig))
        self.assertFalse(limb_ik._single_armature_user(rig))
        self.assert_old_restore_refused(rig, action)

    def test_failure_after_actual_release_restores_current_rest_and_original_history(self):
        self.exercise_fault('release')

    def test_failure_after_actual_bind_restores_current_rest_and_original_history(self):
        self.exercise_fault('bind')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CurrentLegRestAdoptionLateFaultTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError('Late current leg Rest adoption fault tests failed')
