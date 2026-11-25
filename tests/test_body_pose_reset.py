"""Driver protection shared by native rotation and body-reference clearing."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/body_pose_reset.py'


class DriverGuardTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('_driver_guard', SOURCE)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.rig = NS(pose=NS(bones={'hand.L': NS()}))

    def test_transform_or_custom_property_driver_on_region_refuses(self):
        self.rig.pose.bones['hand.L'].path_from_id = lambda: 'pose.bones["hand.L"]'
        for suffix in ('.location', '.rotation_euler', '.rotation_quaternion', '.scale', '["ik_fk"]'):
            self.rig.animation_data = NS(drivers=[NS(data_path='pose.bones["hand.L"]' + suffix, array_index=0)])
            with self.subTest(suffix=suffix):
                with self.assertRaises(ValueError):
                    self.api._guard_drivers(self.rig, {'hand.L'})
        self.rig.animation_data = NS(drivers=[NS(data_path='pose.bones["hand.L"].constraints["Owned"].influence',array_index=0)])
        self.api._guard_drivers(self.rig, {'hand.L'})

    def test_only_exact_prevalidated_owned_driver_keys_are_excluded(self):
        self.rig.pose.bones['hand.L'].path_from_id = lambda: 'pose.bones["hand.L"]'
        path = 'pose.bones["hand.L"].rotation_euler'
        self.rig.animation_data = NS(drivers=[NS(data_path=path,array_index=0)])
        self.api._guard_drivers(self.rig, {'hand.L'}, {(path,0)})
        with self.assertRaises(ValueError):
            self.api._guard_drivers(self.rig, {'hand.L'}, {(path,1)})


if __name__ == '__main__':
    unittest.main()
