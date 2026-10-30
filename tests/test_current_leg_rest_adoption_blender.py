"""Current-controls Rest acceptance in disposable native fixtures only.

Use the trusted background/factory launcher, never an artist file. The real
capture/store record below has native references and complete recovery fields;
its unused packet marker is not a Unity producer/receiver fixture. Restoration
must refuse at the stale native Rest check before any packet file is read.
No guard is mocked. The sole patch injects a failure at the rebuild boundary.
A release runner may preload its frozen product package before this test imports.
"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (
    animation_worklist_target as target, body_original_mode as original,
    body_original_owner as owner, body_region as region,
    body_rest_adoption as adoption, control_pose_assets as poses,
    limb_ik, unity_animation as ua,
)
import test_body_region_blender as regional
import test_original_leg_rest_return_blender as legs
from test_original_return_owner_blender import retain_action


def history(rig, action, motion):
    """Independent native Action/ID/old recovery proof witness."""
    return {
        'session': rig[target.SESSION],
        'refs': tuple(sorted((key, value.as_pointer())
                             for key, value in rig[target.REFS].items())),
        'action_id': action.as_pointer(),
        'action_animation': owner._animation_fingerprint(action),
        'action_properties': regional.properties(action),
        'slots': tuple((slot.handle, slot.identifier) for slot in action.slots),
        'motion_id': motion.as_pointer(),
        'motion_rest': ua._rest_state(motion),
        'motion_properties': regional.properties(motion),
    }


def metadata(rig):
    return {key: rig.data.get(key) for key in (
        limb_ik.DIRECT_REST_KEY, region.RECOVERY_OWNER_KEY, adoption.AUDIT_KEY)}


def body_state(mesh):
    keys = mesh.data.shape_keys
    key_metadata = tuple(
        (key.as_pointer(), key.relative_key.as_pointer(), key.interpolation,
         key.slider_min, key.slider_max, getattr(key, 'lock_shape', False))
        for key in keys.key_blocks) if keys else ()
    return regional.mesh_raw(mesh), key_metadata


def resource_names():
    return {kind: tuple(sorted(getattr(bpy.data, kind).keys()))
            for kind in ('objects', 'meshes', 'collections', 'materials')}


class InjectedCurrentRestFailure(RuntimeError):
    pass


class CurrentLegRestAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background or bpy.data.filepath:
            raise RuntimeError('Use an isolated background factory process, never an artist file.')
        character_designer.register()

    def tearDown(self):
        self.assertIsNone(adoption._ACTIVE)
        self.assertIsNone(region._OWNER_OPERATION)
        self.assertFalse(regional.body_setup._ATOMIC_STACK)

    def make(self):
        rig, mesh = legs.make_fixture()
        paint = mesh.vertex_groups.get('thigh.L') or mesh.vertex_groups.new(name='thigh.L')
        paint.add([0], .371, 'REPLACE')
        paint.lock_weight = True
        sculpt = mesh.shape_key_add(name='Artist asymmetric leg sculpt')
        sculpt.data[0].co.z += .0123
        sculpt.value = .4
        action, motion = retain_action(rig)
        saved = bpy.context.scene.character_designer_animation_worklist
        saved.rig = rig
        saved.workspace_identity = 'current-leg-rest-native-fixture'
        capture = target._capture(bpy.context, rig)
        self.assertEqual(len(capture['targets']), 1)
        target._validate_capture(bpy.context, capture)
        target._store(rig, {
            'version': 1, 'rig': rig, 'scene': bpy.context.scene,
            'view_layer': bpy.context.view_layer.name,
            'workspace_identity': saved.workspace_identity, 'capture': capture,
            'packet_path': 'unused-current-leg-rest-native-fixture.json',
            'packet_sha256': 'f' * 64,
            'motion_data': rig.data, 'motion_rest': ua._rest_state(rig.data),
            'mapped_names': sorted(poses.native_rest(rig)),
        })
        loaded = target._load(rig)
        self.assertIs(loaded['capture']['targets'][0]['data'], rig.data)
        self.assertIs(loaded['motion_data'], rig.data)
        target._prove_motion_data(rig.data, rig.data, capture['targets'][0]['rest'],
                                  loaded['motion_rest'], loaded['mapped_names'])
        self.assertTrue(target.single_armature_user(rig))
        self.assertNotIn(original.SESSION, rig)
        previous = copy.deepcopy(poses.native_rest(rig))
        regional.BodyRegionTests().shorten(rig)
        self.assertNotEqual(poses.native_rest(rig), previous)
        self.assertFalse(target.single_armature_user(rig))
        self.assertFalse(ua._matches_rest_state(rig.data, capture['targets'][0]['rest']))
        return rig, mesh, action, motion, previous

    def assert_old_restore_refused(self, rig, action):
        before = poses.native_rest(rig), metadata(rig), rig[target.SESSION]
        with self.assertRaisesRegex(ValueError, 'saved artist Rest skeleton changed'):
            target.restore_main_rig(bpy.context)
        with self.assertRaisesRegex(ValueError, 'saved artist or owned motion-copy Rest changed'):
            target._read_action_motion_data(rig, action, rig.data)
        self.assertTrue(target.is_adopted(rig))
        self.assertEqual((poses.native_rest(rig), metadata(rig), rig[target.SESSION]), before)

    def rejection_snapshot(self, rig, mesh, action, motion):
        return {
            'rest': ua._rest_state(rig.data), 'metadata': metadata(rig),
            'scene': regional.scene_state(rig, mesh),
            'body': body_state(mesh),
            'history': history(rig, action, motion), 'resources': resource_names(),
            'native_ids': regional.native_ids(rig, mesh),
        }

    def assert_rejected(self, rig, mesh, action, motion, message):
        before = self.rejection_snapshot(rig, mesh, action, motion)
        with self.assertRaisesRegex(ValueError, message):
            adoption.accept_current_legs(bpy.context, rig)
        self.assertEqual(self.rejection_snapshot(rig, mesh, action, motion), before)

    def test_accept_and_second_regional_rebuild_keep_current_rest_paint_and_old_proofs(self):
        rig, mesh, action, motion, previous = self.make()
        current = copy.deepcopy(poses.native_rest(rig))
        body, old_history = body_state(mesh), history(rig, action, motion)
        ids = regional.native_ids(rig, mesh)
        hands = legs.hand_channels(rig, category='ALL')
        result = adoption.accept_current_legs(bpy.context, rig)
        self.assertTrue(result['native_rest_preserved'])
        self.assertFalse(result['old_animation_compatibility_granted'])
        self.assertTrue(adoption.verified_revision(rig))
        self.assertTrue(limb_ik._single_armature_user(rig))
        self.assertFalse(target.single_armature_user(rig))
        self.assertEqual(poses.native_rest(rig), current)
        self.assertNotEqual(poses.native_rest(rig), previous)
        self.assertEqual(body_state(mesh), body)
        self.assertEqual(history(rig, action, motion), old_history)
        self.assertEqual(regional.native_ids(rig, mesh), ids)
        self.assertEqual(legs.hand_channels(rig, category='ALL'), hands)
        self.assert_old_restore_refused(rig, action)
        audit = rig.data[adoption.AUDIT_KEY]
        region.rebuild(bpy.context, rig, 'LEGS')
        self.assertEqual(poses.native_rest(rig), current)
        self.assertEqual(body_state(mesh), body)
        self.assertEqual(history(rig, action, motion), old_history)
        self.assertEqual(rig.data[adoption.AUDIT_KEY], audit)
        self.assertTrue(adoption.verified_revision(rig))
        limb_ik._validate_inventory(rig)
        self.assert_old_restore_refused(rig, action)

    def test_actual_second_armature_binding_refuses_without_writes(self):
        rig, mesh, action, motion, _previous = self.make()
        other = bpy.data.objects.new('Actual second armature binding', rig.data)
        bpy.context.scene.collection.objects.link(other)
        self.assert_rejected(rig, mesh, action, motion, 'actually shares')

    def test_foreign_data_owner_refuses_without_writes(self):
        rig, mesh, action, motion, _previous = self.make()
        other = bpy.data.objects.new('Foreign artist data owner', None)
        bpy.context.scene.collection.objects.link(other)
        other['unapproved_armature_reference'] = rig.data
        self.assert_rejected(rig, mesh, action, motion, 'unknown owner')

    def test_structural_connection_edit_remains_refused(self):
        rig, mesh, action, motion, _previous = self.make()
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['shin.L'].use_connect = False
        bpy.ops.object.mode_set(mode='POSE')
        legs.update(rig)
        self.assert_rejected(rig, mesh, action, motion, 'structure changed')

    def test_authored_leg_animation_remains_refused(self):
        rig, mesh, action, motion, _previous = self.make()
        self.assertTrue(rig.pose.bones['thigh.L'].keyframe_insert('rotation_euler', frame=1))
        authored = rig.animation_data.action
        animation_before = owner._animation_fingerprint(authored)
        self.assert_rejected(rig, mesh, action, motion, 'animates|authored animation')
        self.assertIs(rig.animation_data.action, authored)
        self.assertEqual(owner._animation_fingerprint(authored), animation_before)

    def test_failure_after_registry_acceptance_restores_current_rest_and_old_metadata(self):
        rig, mesh, action, motion, previous = self.make()
        before = self.rejection_snapshot(rig, mesh, action, motion)
        current = copy.deepcopy(poses.native_rest(rig))
        reached = []

        def fail_after_accept(context, actual, region_name):
            self.assertIs(actual, rig)
            self.assertEqual(region_name, 'LEGS')
            self.assertNotEqual(metadata(rig), before['metadata'])
            self.assertTrue(adoption.has_revision(rig))
            limb_ik._validate_inventory(rig)
            self.assertEqual(poses.native_rest(rig), current)
            self.assertEqual(history(rig, action, motion), before['history'])
            reached.append(True)
            raise InjectedCurrentRestFailure('after new Direct registry, before regional rebuild')

        with patch.object(region, 'rebuild', side_effect=fail_after_accept):
            with self.assertRaisesRegex(InjectedCurrentRestFailure, 'after new Direct registry'):
                adoption.accept_current_legs(bpy.context, rig)
        self.assertEqual(reached, [True])
        self.assertEqual(self.rejection_snapshot(rig, mesh, action, motion), before)
        self.assertEqual(poses.native_rest(rig), current)
        self.assertNotEqual(poses.native_rest(rig), previous)
        self.assertFalse(adoption.has_revision(rig))
        self.assertFalse(limb_ik._single_armature_user(rig))
        self.assert_old_restore_refused(rig, action)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CurrentLegRestAdoptionTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError('Current leg Rest adoption tests failed')
