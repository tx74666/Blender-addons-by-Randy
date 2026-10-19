"""One strict attempt per forearm update retains all fresh local proofs.

Run in an isolated native process through run_trusted_blender_validation.py.
These checks use real Direct controls, authored keys and nonzero native wrist
twist. They do not open, repair or save the artist scene.
"""
from array import array
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_mode as original,
    body_rest_resync as resync, control_pose_assets as poses,
    forearm_original_inventory as inventory, forearm_twist as runtime,
    limb_ik, limb_ik_fk)
import test_forearm_unrelated_rest_blender as fixture


def output_bytes(obj):
    """Full managed float32 output, not a tolerance or selected-vertex check."""
    result = []
    for side, record in sorted(runtime._records(obj).items()):
        key = obj.data.shape_keys.key_blocks[record['key']]
        values = array('f', [0.]) * (3 * len(key.data))
        key.data.foreach_get('co', values)
        result.append((side, key.name, float(key.value), bool(key.mute), values.tobytes()))
    return tuple(result)


class ForearmLocalValidationReuseTests(unittest.TestCase):
    # Reuse fixture helpers without inheriting/rerunning the older test cases.
    setUp = fixture.ForearmUnrelatedRestTests.setUp
    tearDown = fixture.ForearmUnrelatedRestTests.tearDown
    ready = fixture.ForearmUnrelatedRestTests.ready
    edit = fixture.ForearmUnrelatedRestTests.edit
    edit_leg = fixture.ForearmUnrelatedRestTests.edit_leg
    snapshot = fixture.ForearmUnrelatedRestTests.snapshot
    assert_active = fixture.ForearmUnrelatedRestTests.assert_active
    assert_local_refuses = fixture.ForearmUnrelatedRestTests.assert_local_refuses
    assert_global_still_rejects = fixture.ForearmUnrelatedRestTests.assert_global_still_rejects

    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def posed(self):
        self.ready()
        self.edit_leg()
        with runtime.defer_runtime(bpy.context, flush_on_exit=False):
            hand = self.rig.pose.bones['hand.L']
            hand.rotation_mode = 'XYZ'
            hand.rotation_euler.y = .57
            bpy.context.view_layer.update()
            self.depsgraph = bpy.context.evaluated_depsgraph_get()

    def test_two_updates_each_take_one_strict_attempt_and_fresh_rest_view(self):
        self.posed()
        before = self.snapshot()
        expected = None
        with patch.object(inventory, 'validate', wraps=inventory.validate) as validate, \
             patch.object(poses, 'native_rest', wraps=poses.native_rest) as rest, \
             patch.object(resync, '_geometry_changes', wraps=resync._geometry_changes) as geometry, \
             patch.object(resync.OriginalRestView, 'validate',
                          autospec=True, side_effect=resync.OriginalRestView.validate) as view, \
             patch.object(limb_ik, '_validate_inventory', wraps=limb_ik._validate_inventory) as graph:
            for _ in range(2):
                for check in (validate, rest, geometry, view, graph):
                    check.reset_mock()
                runtime.update_runtime(bpy.context.scene, self.depsgraph)
                self.assert_active()
                strict = [call for call in validate.call_args_list
                          if call.kwargs.get('original_rest') is None]
                self.assertEqual(len(strict), 1)
                self.assertEqual(validate.call_count, 2)
                self.assertEqual(rest.call_count, 3)
                self.assertEqual(geometry.call_count, 2)
                self.assertEqual(view.call_count, 1)
                self.assertEqual(graph.call_count, 1)
                output = output_bytes(self.mesh)
                if expected is None:
                    expected = output
                else:
                    self.assertEqual(output, expected)
        self.assertEqual(self.snapshot(), before)
        basis = self.mesh.data.shape_keys.reference_key
        key = self.mesh.data.shape_keys.key_blocks[runtime._records(self.mesh)['L']['key']]
        self.assertTrue(any((point.co - basis.data[index].co).length > 1.e-7
                            for index, point in enumerate(key.data)),
                        'Exercise real wrist correction, not an all-Basis shortcut')
        self.assert_global_still_rejects()

    def test_output_bytes_equal_the_previous_double_retry_route(self):
        self.posed()
        before = self.snapshot()
        fallback = inventory._validate_forearm_rest_fallback

        def previous_retry(armature, *, sides=None, dependencies=()):
            # The .60 mesh resolver entered the public validator here. Its
            # extra strict attempt failed before running this same full proof.
            try:
                return inventory.validate(armature)
            except ValueError:
                return fallback(armature, sides=sides, dependencies=dependencies)

        runtime._CACHE.clear()
        runtime._OUTPUT_CACHE.clear()
        runtime.update_runtime(bpy.context.scene, self.depsgraph)
        self.assert_active()
        expected = output_bytes(self.mesh)
        runtime._CACHE.clear()
        runtime._OUTPUT_CACHE.clear()
        with patch.object(inventory, '_validate_forearm_rest_fallback',
                          side_effect=previous_retry), \
             patch.object(inventory, 'validate', wraps=inventory.validate) as validate, \
             patch.object(poses, 'native_rest', wraps=poses.native_rest) as rest:
            runtime.update_runtime(bpy.context.scene, self.depsgraph)
            self.assert_active()
            self.assertEqual(sum(call.kwargs.get('original_rest') is None
                                 for call in validate.call_args_list), 2)
            self.assertEqual(rest.call_count, 4)
        self.assertEqual(output_bytes(self.mesh), expected)
        self.assertEqual(self.snapshot(), before)
        # The following independent call still performs the complete proof.
        runtime._CACHE.clear()
        runtime._OUTPUT_CACHE.clear()
        runtime.update_runtime(bpy.context.scene, self.depsgraph)
        self.assertEqual(output_bytes(self.mesh), expected)

    def test_public_validator_keeps_its_healthy_strict_entry(self):
        self.ready()
        before = self.snapshot()
        with patch.object(inventory, 'validate', wraps=inventory.validate) as validate, \
             patch.object(resync, '_geometry_changes', wraps=resync._geometry_changes) as geometry, \
             patch.object(poses, 'native_rest', wraps=poses.native_rest) as rest:
            result = inventory.validate_forearm(self.rig, sides={'L'})
            self.assertIn(('ARM', 'L'), result['rigs'])
            self.assertEqual(validate.call_count, 1)
            self.assertIsNone(validate.call_args.kwargs.get('original_rest'))
            self.assertEqual(rest.call_count, 1)
            geometry.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_warm_update_rejects_direct_relation_driver_and_mute_edits(self):
        self.posed()
        runtime.update_runtime(bpy.context.scene, self.depsgraph)
        self.assert_active()
        bone = self.rig.data.bones['shin.L']
        scale = bone.inherit_scale
        bone.inherit_scale = 'NONE' if scale != 'NONE' else 'FULL'
        # Deliberately no update_tag, view_layer.update or new graph here.
        self.assert_local_refuses(pattern='(?i)(structure|inherit_scale)')
        bone.inherit_scale = scale
        runtime.update_runtime(bpy.context.scene, self.depsgraph)
        self.assert_active()
        pb, constraint, record = next(entry for entry in limb_ik._owned_constraint_records(self.rig)
                                      if entry[0].name == 'forearm.L')
        self.assertTrue(limb_ik_fk.is_switch_constraint(self.rig, pb, constraint, record))
        driver = next(curve for curve in self.rig.animation_data.drivers
                      if curve.data_path == constraint.path_from_id('influence'))
        expression = driver.driver.expression
        driver.driver.expression = 'ik_fk * 0.5'
        self.assert_local_refuses(pattern='(?i)driver.*edited')
        driver.driver.expression = expression
        runtime.update_runtime(bpy.context.scene, self.depsgraph)
        self.assert_active()
        constraint.mute = False
        self.assert_local_refuses(pattern='(?i)Original constraint.*changed')
        constraint.mute = True
        runtime.update_runtime(bpy.context.scene, self.depsgraph)
        self.assert_active()
        self.assert_global_still_rejects()


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ForearmLocalValidationReuseTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('FOREARM_LOCAL_VALIDATION_REUSE_PASS', result.testsRun, flush=True)
