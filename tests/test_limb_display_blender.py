"""Native FK appearance and one IK Pole/cone-ray visibility policy.

Run in a disposable Blender --background --factory-startup process. These
checks inspect real RNA, saved ownership and evaluated poses; no screenshot or
GPU context is needed, and no artist scene is opened.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_setup, bone_collections as groups,
    limb_fk_visuals as visuals, limb_ik, limb_ik_fk as match,
    limb_ik_fk_batch as batch)
import test_body_calibration_blender as setup
import test_limb_ik_fk_batch_blender as fixtures


def shape_state(rig):
    return {pb.name: limb_ik._pose_shape_json_state(pb) for pb in rig.pose.bones}


def display_state(rig):
    return {'shapes': shape_state(rig), 'marker': rig.data.get(visuals.NATIVE_DISPLAY_KEY),
            'layout': groups.snapshot_layout(rig),
            'custom_shapes': rig.data.show_bone_custom_shapes,
            'display_type': rig.data.display_type}


def collection_flags(rig):
    return {collection.name: (collection.is_visible, collection.is_solo)
            for collection in rig.data.collections_all}


def guide_context():
    return SimpleNamespace(mode='POSE',
        space_data=SimpleNamespace(type='VIEW_3D',
            overlay=SimpleNamespace(show_overlays=True, show_bones=True)))


class LimbDisplayTests(unittest.TestCase):
    # Reuse the proven Direct/Cosha-style calibrated fixture, without inheriting
    # its unrelated rollback suite and running all its tests a second time.
    make = fixtures.BodyLimbBatchTests.make
    pose = fixtures.BodyLimbBatchTests.pose
    assertPose = fixtures.BodyLimbBatchTests.assertPose

    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def bindings(self, rig):
        record = visuals.validate(rig, limb_ik._validate_inventory(rig))
        self.assertIsNotNone(record)
        self.assertEqual(len(record['bindings']), 8)
        return record['bindings']

    def assert_limb_display(self, rig, inventory, key, mode):
        item = inventory['rigs'][key]
        body_members = set(groups.body_collection(rig).bones.keys())
        bindings = visuals.get_record(rig)['bindings']
        for name in item['chain']:
            if mode == 'FK':
                self.assertIn(name, body_members)
                self.assertFalse(rig.data.bones[name].hide, name)
                if hasattr(rig.pose.bones[name], 'hide'):
                    self.assertFalse(rig.pose.bones[name].hide, name)
                self.assertFalse(rig.data.bones[name].hide_select, name)
            else:
                self.assertNotIn(name, body_members)
            if name in bindings:
                expected = None if mode == 'FK' else bpy.data.objects[bindings[name]['object']]
                self.assertEqual(rig.pose.bones[name].custom_shape, expected, name)
        for role in ('target', 'pole'):
            bone = item[role]
            self.assertEqual(bone.name in body_members, mode == 'IK', role)
            self.assertEqual(bone.hide, mode == 'FK', role)
            pb = rig.pose.bones[bone.name]
            if hasattr(pb, 'hide'):
                self.assertEqual(pb.hide, mode == 'FK', role)
        pole = item['pole']
        self.assertEqual(limb_ik._pole_display_visible(guide_context(), rig,
                         pole, rig.pose.bones[pole.name]), mode == 'IK')

    def test_public_roundtrip_shows_native_fk_and_reuses_ik_widgets(self):
        rig, _skin, _surface = self.make()
        bindings = self.bindings(rig)
        widget_pointers = {name: bpy.data.objects[entry['object']].as_pointer()
                           for name, entry in bindings.items()}
        objects = set(bpy.data.objects.keys())
        bones = set(rig.data.bones.keys())
        desired = self.pose(rig)
        for mode in ('IK', 'FK', 'IK'):
            result = batch.switch_all(bpy.context, rig, mode, keyframe=False)
            self.assertFalse(result['keyed'])
            inventory = limb_ik._validate_inventory(rig)
            for key in batch.LIMB_KEYS:
                self.assert_limb_display(rig, inventory, key, mode)
            self.assertPose(rig, desired)
            self.assertEqual(set(rig.data.bones.keys()), bones)
            self.assertEqual(set(bpy.data.objects.keys()), objects)
            self.assertEqual({name: bpy.data.objects[entry['object']].as_pointer()
                              for name, entry in bindings.items()}, widget_pointers)
            if mode == 'FK':
                self.assertEqual(json.loads(rig.data[visuals.NATIVE_DISPLAY_KEY]),
                                 {name: entry['object'] for name, entry in bindings.items()})
            else:
                self.assertNotIn(visuals.NATIVE_DISPLAY_KEY, rig.data)

    def test_repeated_fk_click_repairs_pose_bone_hide_without_autokey(self):
        rig, _skin, _surface = self.make()
        batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        inventory = limb_ik._validate_inventory(rig)
        for item in inventory['rigs'].values():
            for name in item['chain']:
                rig.data.bones[name].hide = True
                rig.data.bones[name].hide_select = True
                if hasattr(rig.pose.bones[name], 'hide'):
                    rig.pose.bones[name].hide = True
        desired = self.pose(rig)
        action = fixtures.action_signature(rig.animation_data.action if rig.animation_data else None)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        result = batch.switch_all(bpy.context, rig, 'FK')
        self.assertFalse(result['changed'])
        self.assertFalse(result['keyed'])
        self.assertPose(rig, desired)
        self.assertEqual(fixtures.action_signature(rig.animation_data.action if rig.animation_data else None), action)
        for key in batch.LIMB_KEYS:
            self.assert_limb_display(rig, inventory, key, 'FK')

    def test_repeated_ik_click_repairs_pole_cone_and_guide_together(self):
        rig, _skin, _surface = self.make()
        inventory = limb_ik._validate_inventory(rig)
        for item in inventory['rigs'].values():
            for role in ('target', 'pole'):
                bone = item[role]
                bone.hide = True
                if hasattr(rig.pose.bones[bone.name], 'hide'):
                    rig.pose.bones[bone.name].hide = True
            pole = item['pole']
            self.assertFalse(limb_ik._pole_display_visible(guide_context(), rig,
                             pole, rig.pose.bones[pole.name]))
        desired = self.pose(rig)
        result = batch.switch_all(bpy.context, rig, 'IK', keyframe=True)
        self.assertFalse(result['changed'])
        self.assertFalse(result['keyed'])
        self.assertPose(rig, desired)
        for key in batch.LIMB_KEYS:
            self.assert_limb_display(rig, inventory, key, 'IK')

    def test_advanced_partial_switch_does_not_override_other_limbs(self):
        rig, _skin, _surface = self.make()
        inventory = limb_ik._validate_inventory(rig)
        other_names = groups._limb_display_names(inventory, batch.LIMB_KEYS[1:])
        before = {name: (limb_ik._pose_shape_json_state(rig.pose.bones[name]),
                        rig.data.bones[name].hide, rig.data.bones[name].hide_select,
                        getattr(rig.pose.bones[name], 'hide', None)) for name in other_names}
        batch.switch_all(bpy.context, rig, 'FK', keys=(batch.LIMB_KEYS[0],), keyframe=False)
        self.assert_limb_display(rig, inventory, batch.LIMB_KEYS[0], 'FK')
        self.assertEqual({name: (limb_ik._pose_shape_json_state(rig.pose.bones[name]),
                         rig.data.bones[name].hide, rig.data.bones[name].hide_select,
                         getattr(rig.pose.bones[name], 'hide', None)) for name in other_names}, before)
        self.assertEqual(set(json.loads(rig.data[visuals.NATIVE_DISPLAY_KEY])),
                         set(inventory['rigs'][batch.LIMB_KEYS[0]]['chain'][:2]))
        limb_ik._validate_inventory(rig)

    def test_cone_ray_policy_uses_mode_pose_hide_shapes_collections_and_overlay(self):
        rig, _skin, _surface = self.make()
        batch.switch_all(bpy.context, rig, 'IK', keyframe=False)
        item = limb_ik._validate_inventory(rig)['rigs'][('ARM', 'L')]
        pole, target = item['pole'], rig.pose.bones[item['target'].name]
        pb, context = rig.pose.bones[pole.name], guide_context()
        visible = lambda: limb_ik._pole_display_visible(context, rig, pole, pb)
        self.assertTrue(visible())
        before = display_state(rig)
        for owner, prop in ((pole, 'hide'), (pb, 'hide'),
                            (rig.data, 'show_bone_custom_shapes'),
                            (context.space_data.overlay, 'show_overlays'),
                            (context.space_data.overlay, 'show_bones')):
            if not hasattr(owner, prop):
                continue
            old = getattr(owner, prop)
            setattr(owner, prop, False if prop.startswith('show_') else True)
            try:
                self.assertFalse(visible(), prop)
            finally:
                setattr(owner, prop, old)
            self.assertTrue(visible(), prop)
        for value in (0., 1e-7):
            target[match.PROPERTY] = value
            try:
                self.assertFalse(visible(), 'FK must never draw an isolated IK ray')
            finally:
                target[match.PROPERTY] = 1.
        widget = pb.custom_shape
        pb.custom_shape = None
        try:
            self.assertFalse(visible(), 'A ray requires its cone custom shape')
        finally:
            pb.custom_shape = widget
        scale = tuple(pb.custom_shape_scale_xyz)
        pb.custom_shape_scale_xyz = (0., 0., 0.)
        try:
            self.assertFalse(visible(), 'A hidden zero-size cone must not leave its ray')
        finally:
            pb.custom_shape_scale_xyz = scale
        style = pole.get(limb_ik.CONTROL_SHAPE_STYLE_KEY)
        pole[limb_ik.CONTROL_SHAPE_STYLE_KEY] = 'SPHERE'
        try:
            self.assertFalse(visible(), 'The sphere style deliberately has no cone or ray')
        finally:
            if style is None:
                pole.pop(limb_ik.CONTROL_SHAPE_STYLE_KEY, None)
            else:
                pole[limb_ik.CONTROL_SHAPE_STYLE_KEY] = style
        body = groups.body_collection(rig)
        body.is_visible = False
        try:
            self.assertFalse(visible(), 'Native collection eye must hide both cone and ray')
        finally:
            body.is_visible = True
        original = rig.data.collections_all['Original']
        original.is_visible, original.is_solo = True, True
        try:
            self.assertFalse(visible(), 'An artist solo collection must hide both cone and ray')
        finally:
            original.is_visible, original.is_solo = False, False
        self.assertTrue(visible())
        self.assertEqual(display_state(rig), before, 'Read-only ray policy must not write RNA')
        limb_ik._validate_inventory(rig)

    def test_artist_shape_and_native_collection_preferences_are_preserved(self):
        rig, _skin = setup.fixture()
        artist_shape = bpy.data.objects.new('Artist Arm Shape', bpy.data.meshes.new('Artist Arm Shape'))
        bpy.context.scene.collection.objects.link(artist_shape)
        rig.pose.bones['upper_arm.L'].custom_shape = artist_shape
        setup.prepare(rig)
        body_setup.generate(bpy.context, rig)
        record = visuals.validate(rig, limb_ik._validate_inventory(rig))
        self.assertNotIn('upper_arm.L', record['bindings'])
        self.assertIn('upper_arm.L', record['skipped'])
        accessories = []
        for name in ('Hair', 'Dress Artist Display'):
            collection = rig.data.collections_all.get(name) or rig.data.collections.new(name)
            collection['artist_note'] = 'Keep this independent display preference'
            collection.is_visible = False
            collection.is_solo = False
            accessories.append(groups._collection_record(collection))
        original = rig.data.collections_all['Original']
        original.is_visible, original.is_solo = True, True
        groups.body_collection(rig).is_visible = False
        flags, display = collection_flags(rig), limb_ik._pose_shape_json_state(rig.pose.bones['upper_arm.L'])
        for mode in ('FK', 'IK'):
            batch.switch_all(bpy.context, rig, mode, keyframe=False)
            self.assertEqual(collection_flags(rig), flags)
            self.assertEqual(limb_ik._pose_shape_json_state(rig.pose.bones['upper_arm.L']), display)
            self.assertEqual(rig.pose.bones['upper_arm.L'].custom_shape, artist_shape)
            self.assertEqual([groups._collection_record(rig.data.collections_all[item['name']])
                              for item in accessories], accessories)
            limb_ik._validate_inventory(rig)

    def test_late_display_failure_rolls_back_shapes_marker_membership_and_pose(self):
        for mode in ('FK', 'IK'):
            rig, _skin, _surface = self.make()
            if mode == 'IK':
                batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
            before, protected = display_state(rig), fixtures.complete(rig)
            desired = self.pose(rig)
            actual_sync, calls = groups.sync_limb_display, []
            def fail_after_sync(*args, **kwargs):
                result = actual_sync(*args, **kwargs)
                calls.append(result)
                raise ValueError('injected final display failure after mutation')
            with patch.object(groups, 'sync_limb_display', side_effect=fail_after_sync):
                with self.assertRaisesRegex(ValueError, 'final display failure'):
                    batch.switch_all(bpy.context, rig, mode, keyframe=False)
            self.assertTrue(calls, 'Failure must occur after the real display writes')
            self.assertEqual(display_state(rig), before)
            self.assertEqual(fixtures.complete(rig), protected)
            self.assertPose(rig, desired)
            limb_ik._validate_inventory(rig)

    def test_repurposed_managed_collection_is_refused_without_display_writes(self):
        rig, _skin, _surface = self.make()
        inventory = limb_ik._validate_inventory(rig)
        # A membership edit is an artist decision, unlike a native eye/solo
        # choice. Do not silently replace it when a mode button is pressed.
        groups.body_collection(rig).assign(rig.data.bones['upper_arm.L'])
        before, protected = display_state(rig), fixtures.complete(rig)
        with self.assertRaisesRegex(ValueError, 'repurposed'):
            groups.sync_limb_display(rig, inventory, force_flags=True)
        self.assertEqual(display_state(rig), before)
        self.assertEqual(fixtures.complete(rig), protected)
        with self.assertRaisesRegex(ValueError, 'repurposed'):
            batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(display_state(rig), before)
        self.assertEqual(fixtures.complete(rig), protected)
        limb_ik._validate_inventory(rig)

    def test_saved_fk_suppression_reopens_and_restores_original_widget_ids(self):
        rig, _skin, _surface = self.make()
        batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        rig_name = rig.name
        before = display_state(rig)
        bindings = self.bindings(rig)
        marker = json.loads(rig.data[visuals.NATIVE_DISPLAY_KEY])
        desired = self.pose(rig)
        with tempfile.TemporaryDirectory(prefix='cd-native-fk-display-') as directory:
            path = str(Path(directory) / 'native-fk.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path), {'FINISHED'})
            rig = bpy.data.objects[rig_name]
            self.assertEqual(display_state(rig), before)
            self.assertEqual(json.loads(rig.data[visuals.NATIVE_DISPLAY_KEY]), marker)
            self.assertEqual(visuals.validate(rig, limb_ik._validate_inventory(rig))['bindings'], bindings)
            self.assertPose(rig, desired)
            result = batch.switch_all(bpy.context, rig, 'IK', keyframe=False)
            self.assertTrue(result['changed'])
            self.assertNotIn(visuals.NATIVE_DISPLAY_KEY, rig.data)
            for name, entry in bindings.items():
                self.assertEqual(rig.pose.bones[name].custom_shape, bpy.data.objects[entry['object']])
            limb_ik._validate_inventory(rig)

    def test_missing_or_forged_native_marker_is_not_accepted_as_ownership(self):
        rig, _skin, _surface = self.make()
        batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        marker = rig.data[visuals.NATIVE_DISPLAY_KEY]
        rig.data.pop(visuals.NATIVE_DISPLAY_KEY)
        try:
            with self.assertRaises(limb_ik.LimbIKError):
                limb_ik._validate_inventory(rig)
            saved = json.loads(marker)
            name = next(iter(saved))
            saved[name] = 'An artist object is not the owned ring'
            rig.data[visuals.NATIVE_DISPLAY_KEY] = json.dumps(saved)
            with self.assertRaises(limb_ik.LimbIKError):
                limb_ik._validate_inventory(rig)
        finally:
            rig.data[visuals.NATIVE_DISPLAY_KEY] = marker
        limb_ik._validate_inventory(rig)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LimbDisplayTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print('LIMB_DISPLAY_PASSED', result.testsRun if result.wasSuccessful() else 0, flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)
