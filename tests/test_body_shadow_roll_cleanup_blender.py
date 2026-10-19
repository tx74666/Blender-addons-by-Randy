"""Native shadow-roll capture failures leave a small retained source exact.

Run only in an isolated background factory process. A wrapper must preload the
frozen package; these tests do not exercise public operator Undo or artist files.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import character_designer
from character_designer import (body_original_mode as original, body_original_owner,
    body_region, body_setup_transaction as transaction, bone_display_sync,
    forearm_twist, limb_ik, unity_animation as ua)
from test_original_leg_rest_return_blender import make_fixture, edit_legs
from test_original_return_owner_blender import retain_action
import test_body_region_blender as regional


def resources():
    return {kind: {value.name: (value.as_pointer(), value.users) for value in getattr(bpy.data, kind)}
            for kind in ('objects', 'armatures', 'meshes', 'collections', 'materials', 'actions', 'shape_keys')}


def context_state(rig):
    active = bpy.context.view_layer.objects.active
    return {'mode': bpy.context.mode, 'scene': bpy.context.scene.as_pointer(),
        'view_layer': bpy.context.view_layer.as_pointer(),
        'active': active.as_pointer() if active else None,
        'selected': sorted((obj.name, obj.as_pointer()) for obj in bpy.context.selected_objects),
        'active_bone': rig.data.bones.active.name if rig.data.bones.active else None,
        # Selection belongs to PoseBone outside Edit Mode in Blender 5.2.
        'bone_flags': {pb.name: (bool(pb.select), bool(pb.bone.hide), bool(pb.bone.hide_select),
            bool(pb.hide) if hasattr(pb, 'hide') else None) for pb in rig.pose.bones}}


def protected(rig, mesh, action, motion):
    return {'scene': regional.scene_state(rig, mesh), 'context': context_state(rig),
        'full_rest': ua._rest_state(rig.data), 'native_ids': regional.native_ids(rig, mesh),
        'action': body_original_owner._animation_fingerprint(action),
        'motion': ua._rest_state(motion), 'resources': resources(),
        'owner': body_region.verified_single_armature_user(rig),
        'busy': (bone_display_sync._BUSY, forearm_twist._BUSY)}


class ShadowCaptureFailure(RuntimeError):
    pass


class BodyShadowRollCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background or '--factory-startup' not in sys.argv or bpy.data.filepath:
            raise RuntimeError('Use only a disposable background factory-startup process without an artist file.')
        character_designer.register()

    def make(self):
        rig, mesh = make_fixture()
        action, motion = retain_action(rig)
        original.enter(bpy.context, rig)
        edit_legs(rig)
        self.assertTrue(original.leave(bpy.context, rig))
        self.assertTrue(body_region.verified_single_armature_user(rig))
        self.assertEqual(rig.data.users, 2)
        return rig, mesh, action, motion

    def check_failure(self, target_mode):
        rig, mesh, action, motion = self.make()
        for source_mode in ('OBJECT', 'POSE'):
            with self.subTest(source_mode=source_mode, failure_after=target_mode):
                limb_ik._mode_set(bpy.context, rig, source_mode)
                old_sync, old_corrective = bone_display_sync._BUSY, forearm_twist._BUSY
                # Also prove nested pauses retain an already busy caller.
                bone_display_sync._BUSY = forearm_twist._BUSY = source_mode == 'POSE'
                try:
                    before = protected(rig, mesh, action, motion)
                    held = {bone.name: (bone, bone.as_pointer(), rig.pose.bones[bone.name],
                        rig.pose.bones[bone.name].as_pointer(),
                        [(con, con.as_pointer()) for con in rig.pose.bones[bone.name].constraints])
                        for bone in rig.data.bones}
                    real = limb_ik._mode_set
                    injected = []

                    def fail_once_after_real_switch(context, current, mode):
                        result = real(context, current, mode)
                        if current is not rig:
                            self.assertIsNot(current.data, rig.data)
                            self.assertFalse(rig.data.is_editmode)
                            self.assertFalse(rig.select_get())
                            self.assertEqual(tuple(context.selected_objects), (current,))
                            self.assertTrue(bone_display_sync._BUSY)
                            self.assertTrue(forearm_twist._BUSY)
                            self.assertIsNone(current.data.animation_data)
                            self.assertFalse(tuple(current.data.keys()))
                            if mode == target_mode and not injected:
                                injected.append((current.name, mode))
                                raise ShadowCaptureFailure('after real shadow ' + mode)
                        return result

                    with patch.object(limb_ik, '_mode_set', side_effect=fail_once_after_real_switch):
                        with self.assertRaisesRegex(ShadowCaptureFailure, 'after real shadow ' + target_mode):
                            transaction.capture(bpy.context, rig)
                    self.assertEqual(len(injected), 1)
                    for name, (bone, bone_pointer, pb, pose_pointer, constraints) in held.items():
                        self.assertEqual(bone.as_pointer(), bone_pointer, name)
                        self.assertEqual(rig.data.bones[name].as_pointer(), bone_pointer, name)
                        self.assertEqual(pb.as_pointer(), pose_pointer, name)
                        self.assertEqual(rig.pose.bones[name].as_pointer(), pose_pointer, name)
                        for con, pointer in constraints:
                            self.assertEqual(con.as_pointer(), pointer, name + '/' + con.name)
                    after = protected(rig, mesh, action, motion)
                    changed = [field for field in before if before[field] != after[field]]
                    self.assertFalse(changed, 'Changed protected source fields after shadow failure: ' + repr(changed))
                    self.assertTrue(after['owner'])
                    self.assertFalse(rig.data.is_editmode)
                    print('BODY_SHADOW_FAILURE_CLEANUP', source_mode, target_mode,
                          'source, RNA, owner, users, context, pauses and resources exact', flush=True)
                finally:
                    bone_display_sync._BUSY, forearm_twist._BUSY = old_sync, old_corrective

    def test_capture_failure_after_real_shadow_edit_recovers_object_and_pose_sources(self):
        self.check_failure('EDIT')

    def test_capture_failure_after_real_shadow_object_exit_recovers_object_and_pose_sources(self):
        self.check_failure('OBJECT')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BodyShadowRollCleanupTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('Shadow capture fault cleanup tests failed.')
