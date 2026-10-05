"""Stable Pole shafts and native keyed-mode display following.

Run only in a disposable Blender --background --factory-startup process.
This adds coverage for real Stable VIS RNA and frame_change_post behavior;
it never opens an artist scene or requires a GPU drawing context.
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (bone_collections as groups, bone_display, control_pose_assets,
    limb_fk_visuals as visuals, limb_ik, limb_ik_fk as match,
    limb_ik_fk_batch as batch)
import test_bone_collections_blender as stable
import test_limb_ik_fk_batch_blender as direct


def flags(rig):
    return {collection.name: (collection.is_visible, collection.is_solo)
            for collection in rig.data.collections_all}


def context_for_guide():
    return SimpleNamespace(mode='POSE',
        space_data=SimpleNamespace(type='VIEW_3D',
            overlay=SimpleNamespace(show_overlays=True, show_bones=True)))


class LimbDisplayFollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def make_stable(self):
        rig, _settings, _original = stable.fixture('ROLL_DECOUPLED')
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        self.assertEqual(bpy.ops.character_designer.limb_ik_build_all(), {'FINISHED'})
        visuals.build(bpy.context, rig)
        inventory = limb_ik._validate_inventory(rig)
        self.assertEqual(inventory['schema'], limb_ik.ROLL_DECOUPLED_SCHEMA)
        self.assertEqual(set(inventory['rigs']), set(batch.LIMB_KEYS))
        self.assertTrue(all(item.get('line') for item in inventory['rigs'].values()))
        return rig

    def make_direct(self):
        fixture = direct.BodyLimbBatchTests(methodName='runTest')
        rig, _skin, _surface = fixture.make()
        return rig

    def assert_mode_display(self, rig, mode, *, sphere_keys=()):
        inventory = limb_ik._validate_inventory(rig)
        members = set(groups.body_collection(rig).bones.keys())
        record = visuals.get_record(rig)
        for key, item in inventory['rigs'].items():
            self.assertEqual(match.mode_for_rig(rig, item), mode)
            for name in item['chain']:
                self.assertEqual(name in members, mode == 'FK', name)
                if mode == 'FK':
                    self.assertFalse(rig.data.bones[name].hide, name)
                    self.assertFalse(getattr(rig.pose.bones[name], 'hide', False), name)
                    self.assertFalse(rig.data.bones[name].hide_select, name)
                if name in record['bindings']:
                    widget = bpy.data.objects[record['bindings'][name]['object']]
                    self.assertEqual(rig.pose.bones[name].custom_shape,
                                     None if mode == 'FK' else widget, name)
            for role in ('target', 'pole'):
                bone = item[role]
                self.assertEqual(bone.name in members, mode == 'IK', bone.name)
                self.assertEqual(bone.hide, mode == 'FK', bone.name)
                self.assertEqual(getattr(rig.pose.bones[bone.name], 'hide', False),
                                 mode == 'FK', bone.name)
            line = item.get('line')
            if line is not None:
                shaft_visible = mode == 'IK' and key not in sphere_keys
                self.assertEqual(line.name in members, shaft_visible, line.name)
                self.assertEqual(line.hide, not shaft_visible, line.name)
                self.assertEqual(getattr(rig.pose.bones[line.name], 'hide', False),
                                 not shaft_visible, line.name)
                self.assertTrue(line.hide_select, 'The display-only shaft must remain unselectable')
        return inventory

    def test_stable_roundtrip_restores_native_bones_and_both_shaft_hide_flags(self):
        rig = self.make_stable()
        native_rest = control_pose_assets.native_rest(rig)
        widgets = {name: bpy.data.objects[entry['object']].as_pointer()
                   for name, entry in visuals.get_record(rig)['bindings'].items()}
        resources, bones = set(bpy.data.objects.keys()), set(rig.data.bones.keys())
        for mode in ('IK', 'FK', 'IK', 'FK'):
            inventory = limb_ik._validate_inventory(rig)
            # Reproduce the legacy split hide flags on the incoming display.
            for item in inventory['rigs'].values():
                names = item['chain'] if mode == 'FK' else (item['pole'].name, item['line'].name)
                for name in names:
                    rig.data.bones[name].hide = True
                    if hasattr(rig.pose.bones[name], 'hide'):
                        rig.pose.bones[name].hide = True
            self.assertEqual(bpy.ops.character_designer.body_ik_fk_switch(mode=mode), {'FINISHED'})
            self.assert_mode_display(rig, mode)
            self.assertEqual(control_pose_assets.native_rest(rig), native_rest)
            self.assertEqual(set(bpy.data.objects.keys()), resources)
            self.assertEqual(set(rig.data.bones.keys()), bones)
            self.assertEqual({name: bpy.data.objects[entry['object']].as_pointer()
                              for name, entry in visuals.get_record(rig)['bindings'].items()}, widgets)

    def test_stable_sphere_style_retains_coherent_mode_display_and_restores_arrow(self):
        rig = self.make_stable()
        item = limb_ik._validate_inventory(rig)['rigs'][('ARM', 'L')]
        for pb in rig.pose.bones:
            pb.select = False
        pole = rig.pose.bones[item['pole'].name]
        pole.select = True
        rig.data.bones.active = pole.bone
        for style, sphere_keys in (('SPHERE', (('ARM', 'L'),)), ('ARROW', ())):
            self.assertEqual(bpy.ops.character_designer.limb_ik_set_control_shape(style=style), {'FINISHED'})
            self.assert_mode_display(rig, 'IK', sphere_keys=sphere_keys)
            self.assertEqual(limb_ik._pole_display_visible(context_for_guide(), rig, pole.bone, pole),
                             style == 'ARROW', 'Cone/shaft visibility must change before another mode click')
            for mode in ('IK', 'FK', 'IK'):
                batch.switch_all(bpy.context, rig, mode, keyframe=False)
                self.assert_mode_display(rig, mode, sphere_keys=sphere_keys)
            self.assertEqual(limb_ik._pole_display_visible(context_for_guide(), rig, pole.bone, pole),
                             style == 'ARROW')

    def test_stable_style_display_failure_restores_membership_widget_and_flags(self):
        rig = self.make_stable()
        item = limb_ik._validate_inventory(rig)['rigs'][('ARM', 'L')]
        for pb in rig.pose.bones:
            pb.select = False
        pole = rig.pose.bones[item['pole'].name]
        pole.select = True
        rig.data.bones.active = pole.bone
        before = bone_display._snapshot(rig)
        shape = limb_ik._pose_shape_json_state(pole)
        resources = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())
        actual_sync, calls = groups.sync_limb_display, []
        def fail_after_sync(*args, **kwargs):
            result = actual_sync(*args, **kwargs)
            calls.append(result)
            raise ValueError('injected Stable style display failure after mutation')
        with patch.object(groups, 'sync_limb_display', side_effect=fail_after_sync):
            self.assertEqual(bpy.ops.character_designer.limb_ik_set_control_shape(style='SPHERE'), {'CANCELLED'})
        self.assertTrue(calls)
        self.assertTrue(calls[0]['membership_changed'], 'Failure must follow the actual shaft membership handoff')
        self.assertEqual(bone_display._snapshot(rig), before)
        self.assertEqual(limb_ik._pose_shape_json_state(pole), shape)
        self.assertNotIn(limb_ik.CONTROL_SHAPE_STYLE_KEY, pole.bone)
        self.assertEqual((set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())), resources)
        self.assert_mode_display(rig, 'IK')

    def test_keyed_fk_ik_frames_follow_mode_without_rewriting_collection_preferences(self):
        for method in ('STABLE', 'DIRECT'):
            with self.subTest(method=method):
                rig = self.make_stable() if method == 'STABLE' else self.make_direct()
                batch.switch_all(bpy.context, rig, 'IK', keyframe=False)
                inventory = limb_ik._validate_inventory(rig)
                native_rest = control_pose_assets.native_rest(rig)
                for item in inventory['rigs'].values():
                    target = rig.pose.bones[item['target'].name]
                    for frame, value in ((1, 1.), (10, 0.), (20, 1.)):
                        target[match.PROPERTY] = value
                        self.assertTrue(target.keyframe_insert(data_path='["ik_fk"]', frame=frame))
                for curve in limb_ik._fcurves_for_action(rig.animation_data.action):
                    for point in curve.keyframe_points:
                        point.interpolation = 'CONSTANT'
                original = rig.data.collections_all['Original']
                original.is_visible, original.is_solo = True, True
                groups.body_collection(rig).is_visible = False
                preferences = flags(rig)
                groups._FRAME_CACHE.clear()
                for frame, mode in ((1, 'IK'), (10, 'FK'), (20, 'IK'), (10, 'FK'), (1, 'IK')):
                    bpy.context.scene.frame_set(frame)
                    current = self.assert_mode_display(rig, mode)
                    self.assertEqual(flags(rig), preferences)
                    self.assertEqual(control_pose_assets.native_rest(rig), native_rest)
                    for item in current['rigs'].values():
                        self.assertFalse(limb_ik._pole_display_visible(context_for_guide(), rig,
                                         item['pole'], rig.pose.bones[item['pole'].name]),
                                         'The artist solo/eye must keep cone and ray invisible together')
                    if mode == 'FK':
                        self.assertEqual(set(json.loads(rig.data[visuals.NATIVE_DISPLAY_KEY])),
                                         set(visuals.get_record(rig)['bindings']))
                    else:
                        self.assertNotIn(visuals.NATIVE_DISPLAY_KEY, rig.data)
                self.assertEqual(bpy.app.handlers.frame_change_post.count(groups._frame_visibility), 1)

    def test_steady_frame_keeps_manual_pose_hide_and_repurposed_body_membership(self):
        rig = self.make_stable()
        batch.switch_all(bpy.context, rig, 'IK', keyframe=False)
        groups._frame_visibility(bpy.context.scene, objects=(rig,))
        inventory = limb_ik._validate_inventory(rig)
        item = inventory['rigs'][('ARM', 'L')]
        pole = rig.pose.bones[item['pole'].name]
        pole.hide = True
        groups._frame_visibility(bpy.context.scene, objects=(rig,))
        self.assertTrue(pole.hide, 'A steady frame must not undo a manual native hide')
        self.assertFalse(limb_ik._pole_display_visible(context_for_guide(), rig, pole.bone, pole))
        body = groups.body_collection(rig)
        body.assign(rig.data.bones[item['chain'][0]])
        members = set(body.bones.keys())
        rig.pose.bones[item['target'].name][match.PROPERTY] = 0.
        groups._frame_visibility(bpy.context.scene, objects=(rig,))
        self.assertEqual(set(body.bones.keys()), members,
                         'Frame following must preserve repurposed artist membership')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LimbDisplayFollowTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print('LIMB_DISPLAY_FOLLOW_PASSED', result.testsRun if result.wasSuccessful() else 0, flush=True)
    if not result.wasSuccessful():
        raise SystemExit(1)
