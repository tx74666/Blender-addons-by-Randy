"""The wrist reset targets the hand axes measured in native Edit Mode."""
import math
from pathlib import Path
import sys
import unittest

import bpy
from mathutils import Vector

if 'character_designer' not in sys.modules:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
from character_designer import limb_ik_wrist_rotation as wrist


def angle(first, second):
    a = first.to_quaternion().normalized()
    b = second.to_quaternion().normalized()
    d = a.conjugated() @ b
    return 2 * math.atan2(math.hypot(d.x, d.y, d.z), abs(d.w))


class WristEditReferenceTests(unittest.TestCase):
    def test_rest_axes_ignore_posed_forearms_and_preserve_hand_location_and_scale(self):
        data = bpy.data.armatures.new('Wrist test skeleton')
        rig = bpy.data.objects.new('Wrist test character', data)
        bpy.context.collection.objects.link(rig)
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        try:
            bpy.ops.object.mode_set(mode='EDIT')
            edit_orientations = {}
            for side, sign, roll in [('L', 1., .57), ('R', -1., -.32)]:
                arm = data.edit_bones.new('forearm.' + side)
                arm.head, arm.tail = (.4 * sign, .05, 1.2), (.7 * sign, .07, 1.0)
                hand = data.edit_bones.new('hand.' + side)
                hand.parent, hand.use_connect = arm, True
                hand.head, hand.tail = arm.tail, arm.tail + Vector((.16 * sign, .04, -.075))
                hand.roll = roll
                edit_orientations[hand.name] = hand.matrix.copy()
            bpy.ops.object.mode_set(mode='POSE')
            rig.rotation_euler = (.41, -.22, .34)
            for sign, side in [(1., 'L'), (-1., 'R')]:
                arm = rig.pose.bones['forearm.' + side]
                arm.rotation_mode = 'XYZ'
                arm.rotation_euler = (.51 * sign, .33, -.26 * sign)
                hand = rig.pose.bones['hand.' + side]
                hand.rotation_mode = 'XYZ'
                hand.rotation_euler = (-.3, .22 * sign, .14)
                hand.scale = (.72, 1.13, .95)
            bpy.context.view_layer.update()
            for name, edited in edit_orientations.items():
                hand = rig.pose.bones[name]
                with self.subTest(hand=name):
                    before = hand.matrix.copy()
                    self.assertGreater(angle(before, edited), .1)
                    result = wrist._reference(None, rig, hand)[name]
                    self.assertLess(angle(result, edited), 2e-6)
                    self.assertLess((result.translation - before.translation).length, 2e-6)
                    self.assertLess((result.to_scale() - before.to_scale()).length, 2e-6)
                    self.assertEqual(before, hand.matrix)
        finally:
            if rig.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.data.objects.remove(rig, do_unlink=True)
            bpy.data.armatures.remove(data)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WristEditReferenceTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
