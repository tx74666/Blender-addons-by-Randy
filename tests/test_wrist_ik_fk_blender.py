"""Parent-space wrist rotation: matching, blending, animation and rollback."""
import math
import sys
import tempfile
import unittest
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import limb_ik, limb_ik_fk as switching, root_control
import test_limb_ik_fk_blender as base


def update(rig):
    switching._update(bpy.context, rig)


def native(rig):
    update(rig)
    return {pb.name: pb.matrix.copy() for pb in rig.pose.bones if not pb.bone.get(limb_ik.OWNER_KEY)}


def fixture(method, side, *, with_root=True, rotation_space='PARENT_DELTA'):
    base.base.ensure_registered()
    build_plan = limb_ik._build_plan
    if rotation_space == 'LOCAL':
        def legacy_plan(*args, **kwargs):
            plan = build_plan(*args, **kwargs)
            plan.auto_rotation_space = 'LOCAL'
            return plan
        limb_ik._build_plan = legacy_plan
    try:
        rig, key, data = base.build(method, 'LEFT_ARM' if side == 'L' else 'RIGHT_ARM')
    finally:
        limb_ik._build_plan = build_plan
    assert data.get('auto_rotation_space', 'LOCAL') == rotation_space, data.get('auto_rotation_space')
    # A native child catches hand matching that preserves only the three chain bones.
    bpy.ops.object.mode_set(mode='EDIT')
    hand = rig.data.edit_bones[data['chain'][-1]]
    base.base.add_bone(rig.data.edit_bones, 'Wrist Test Finger.' + side, hand.tail,
                       hand.tail + Vector((.08 if side == 'L' else -.08, 0, 0)), hand)
    bpy.ops.object.mode_set(mode='POSE')
    root = None
    if with_root:
        record = root_control.build(bpy.context, rig)
        root = rig.pose.bones[record['master']]
        root.rotation_mode = 'XYZ'
        root.location, root.rotation_euler = (.13, -.06, .09), (.21, -.17, .32)
        if root_control.SCALE_PROPERTY in root:
            root[root_control.SCALE_PROPERTY] = 1.17
        else:
            root.scale = (1.17,) * 3
    rig.location, rig.rotation_euler, rig.scale = (.2, -.3, .1), (.19, -.11, .27), (.83,) * 3
    data = limb_ik._validate_inventory(rig)['rigs'][key]
    target = rig.pose.bones[data['target'].name]
    target.location += Vector((-.035 if side == 'L' else .035, -.095, .09))
    target.rotation_mode = 'XYZ'
    target.rotation_euler = (.31, -.27, .42)
    update(rig)
    assert target.custom_shape_transform == rig.pose.bones[data['chain'][-1]]
    return rig, key, data, root


class WristMatchingTests(unittest.TestCase):
    def test_legacy_local_wrist_matching_remains_supported(self):
        for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
            with self.subTest(method=method):
                rig, key, data, _root = fixture(method, 'L', rotation_space='LOCAL')
                self.assertEqual(data['auto_offset_rotation'].type, 'COPY_ROTATION')
                desired = native(rig)
                for mode in ('FK', 'IK'):
                    switching.switch_limb(bpy.context, rig, key, mode, keyframe=False)
                    switching._verify(rig, desired)
                    self.assertEqual(limb_ik._validate_inventory(rig)['rigs'][key]['auto_rotation_space'], 'LOCAL')

    def test_parent_delta_roundtrip_preserves_fingers_and_root_pose(self):
        for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
            for side in ('L', 'R'):
                with self.subTest(method=method, side=side):
                    rig, key, data, _root = fixture(method, side)
                    desired = native(rig)
                    rests = {b.name: tuple(tuple(row) for row in b.matrix_local) for b in rig.data.bones}
                    target = rig.pose.bones[data['target'].name]
                    display = (target.custom_shape, target.custom_shape_transform,
                               tuple(target.custom_shape_translation), tuple(target.custom_shape_rotation_euler),
                               tuple(target.custom_shape_scale_xyz))
                    for mode in ('FK', 'IK'):
                        switching.switch_limb(bpy.context, rig, key, mode, keyframe=False)
                        switching._verify(rig, desired)
                        limb_ik._validate_inventory(rig)
                    self.assertEqual({b.name: tuple(tuple(row) for row in b.matrix_local) for b in rig.data.bones}, rests)
                    self.assertEqual((target.custom_shape, target.custom_shape_transform,
                                      tuple(target.custom_shape_translation), tuple(target.custom_shape_rotation_euler),
                                      tuple(target.custom_shape_scale_xyz)), display)

    def test_parent_delta_blend_matches_both_endpoints(self):
        for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
            for side in ('L', 'R'):
                for endpoint in ('FK', 'IK'):
                    with self.subTest(method=method, side=side, endpoint=endpoint):
                        rig, key, data, _root = fixture(method, side)
                        target = rig.pose.bones[data['target'].name]
                        target[switching.PROPERTY] = .5
                        desired = native(rig)
                        switching.switch_limb(bpy.context, rig, key, endpoint, keyframe=False)
                        switching._verify(rig, desired)
                        self.assertEqual(switching.mode_for_rig(rig, data), endpoint)

    def test_parent_delta_failed_match_restores_offset_and_channels(self):
        rig, key, data, _root = fixture('DIRECT_PREROLL', 'L')
        switching.switch_limb(bpy.context, rig, key, 'FK', keyframe=False)
        desired = native(rig)
        before = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones}
        offset = data['auto_offset_rotation']
        mute = offset.mute
        helper = limb_ik._auto_target_rotation_matrix
        def fail(*_args):
            raise RuntimeError('Injected parent-delta wrist matching failure')
        limb_ik._auto_target_rotation_matrix = fail
        try:
            with self.assertRaisesRegex(RuntimeError, 'parent-delta wrist'):
                switching.switch_limb(bpy.context, rig, key, 'IK', keyframe=False)
        finally:
            limb_ik._auto_target_rotation_matrix = helper
        self.assertEqual(switching.mode_for_rig(rig, data), 'FK')
        self.assertEqual(offset.mute, mute)
        self.assertLess(max(abs(pb.matrix_basis[i][j] - before[pb.name][i][j])
                            for pb in rig.pose.bones for i in range(4) for j in range(4)), 1e-6)
        switching._verify(rig, desired)
        limb_ik._validate_inventory(rig)

    def test_parent_delta_keyed_roundtrip_and_reopen(self):
        for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
            with self.subTest(method=method):
                rig, key, data, _root = fixture(method, 'R')
                target = rig.pose.bones[data['target'].name]
                bpy.context.scene.frame_set(1)
                desired = native(rig)
                target.keyframe_insert('location', frame=1)
                target.keyframe_insert('rotation_euler', frame=1)
                bpy.context.scene.frame_set(10)
                switching.switch_limb(bpy.context, rig, key, 'FK', keyframe=True)
                bpy.context.scene.frame_set(20)
                switching.switch_limb(bpy.context, rig, key, 'IK', keyframe=True)
                mode_path = switching.property_path(target)
                curves = limb_ik._fcurves_for_action(rig.animation_data.action)
                mode_curve = next(c for c in curves if c.data_path == mode_path)
                self.assertTrue(all(k.interpolation == 'CONSTANT' for k in mode_curve.keyframe_points))
                self.assertIn(data['auto_offset_rotation'].path_from_id('influence'), switching.owned_driver_paths(rig))
                for frame, expected in ((1, 'IK'), (9.99, 'IK'), (10, 'FK'), (19.99, 'FK'), (20, 'IK')):
                    bpy.context.scene.frame_set(math.floor(frame), subframe=frame % 1)
                    update(rig)
                    self.assertEqual(switching.mode_for_rig(rig, data), expected)
                    switching._verify(rig, desired)
                name = rig.name
                with tempfile.TemporaryDirectory(prefix='cd-wrist-switch-') as folder:
                    path = str(Path(folder) / 'wrist.blend')
                    bpy.ops.wm.save_as_mainfile(filepath=path)
                    bpy.ops.wm.open_mainfile(filepath=path)
                    rig = bpy.data.objects[name]
                    data = limb_ik._validate_inventory(rig)['rigs'][key]
                    self.assertEqual(data['auto_rotation_space'], 'PARENT_DELTA')
                    switching._verify(rig, desired)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WristMatchingTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('WRIST_IK_FK_PASSED', result.testsRun, flush=True)
