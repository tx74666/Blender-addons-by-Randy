"""Full-selection Alt+R preserves location and scale and can roll back its calibration."""
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


class Rig(dict):
    pass


class NaturalPoseReset(unittest.TestCase):
    def setUp(self):
        package = ModuleType('_explicit_reset_test')
        package.__path__ = [str(Path(__file__).resolve().parents[1] / 'addons/character_designer')]
        self.bpy = ModuleType('bpy')
        self.ops = self.bpy.ops = NS(pose=Mock())
        self.enterContext(patch.dict(sys.modules, {package.__name__: package, 'bpy': self.bpy}))
        self.clear = importlib.import_module(package.__name__ + '.natural_pose_clear')
        self.rig = Rig()
        bones = [NS(name=name, scale=(0.961,)*3) for name in ('root', 'target', 'pole')]
        self.rig.pose = NS(bones={pb.name: pb for pb in bones})
        self.context = NS(object=self.rig, selected_pose_bones=bones)
        package.root_control = NS(get_record=lambda rig: {'master': 'root'})
        self.inventory = {'rigs': {'ARM.L': {'target': bones[1], 'pole': bones[2]}}}
        package.natural_limb_inventory = NS(prepare=Mock(return_value=self.inventory))
        self.mode = package.natural_bind_mode = Mock()
        self.graph = package.natural_bind_graph = Mock(KEY='graph')
        self.graph.solver_kind.return_value = 'STABLE_MCH_ORI'
        self.solver = package.natural_limb_solve = Mock()
        package.natural_body_reference = Mock()
        package.controller_transform_clear = NS(_ROTATION_FIELDS=(), _BEND_FIELDS=())
        self.update = package.limb_ik_fk = Mock()
        self.clear.body_selected = Mock(return_value=True)
        self.clear._pose_inputs = lambda rig: {n: {'scale': b.scale} for n, b in rig.pose.bones.items()}
        self.ops.pose.loc_clear.return_value = self.ops.pose.rot_clear.return_value = {'FINISHED'}

        def reset_scale(*args):
            bones[1].scale = (1.0,)*3
            return {'FINISHED'}

        self.ops.pose.scale_clear.side_effect = reset_scale

    def test_rotation_clear_captures_before_native_clear_and_uses_the_verified_bind_graph(self):
        self.mode.capture.side_effect = lambda ctx, rig: rig.pose.bones['target'].scale
        self.clear._clear_selected_rotations(self.context, None)
        self.mode.begin_preview.assert_called_once_with(self.context, self.rig, (0.961,)*3)
        for op in [self.ops.pose.loc_clear, self.ops.pose.scale_clear, self.ops.pose.rot_clear]:
            op.assert_called_once_with('EXEC_DEFAULT', False)
        self.graph.install.assert_called_once_with(self.context, self.rig, self.inventory, solver='STABLE_MCH_ORI')
        self.solver.verify_bind.assert_called_once_with(self.rig, self.inventory)
        self.assertEqual(self.rig.pose.bones['target'].scale, (0.961,)*3)
        self.mode.hold_control_channels.assert_called_once_with(
            self.context, self.rig, self.context.selected_pose_bones, ('location', 'scale'))

    def test_invalid_selection_never_starts_or_captures_the_full_solve(self):
        self.clear.body_selected.return_value = False
        with self.assertRaisesRegex(ValueError, 'Select the root'):
            self.clear._clear_selected_rotations(self.context, None)
        self.mode.capture.assert_not_called()
        self.ops.pose.loc_clear.assert_not_called()
        self.graph.install.assert_not_called()

    def test_failed_bind_verification_restores_pose_and_removes_only_new_graph(self):
        self.solver.verify_bind.side_effect = ValueError('Failed bind verification')
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.rig['graph'] = {'token': 'original'}
                self.graph.reset_mock()
                with self.assertRaisesRegex(ValueError, 'Failed bind verification'):
                    self.clear._clear_selected_rotations(self.context, None)
                self.assertEqual(self.rig.pose.bones['target'].scale, (0.961,)*3)
                self.assertEqual(self.graph.remove.called, not existing)
                self.assertEqual(self.graph.recalibrate.return_value.called, existing)
                self.mode.begin_preview.assert_not_called()
