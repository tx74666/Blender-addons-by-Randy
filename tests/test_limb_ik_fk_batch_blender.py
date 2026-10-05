"""Real Direct native-FK batch switches and all-or-none animation rollback.

Run only in a disposable Blender --background --factory-startup process.
"""
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Euler, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_calibration, body_original_mode as original,
    body_rest_resync, body_setup, bone_display, character_setup, control_pose_assets,
    foot_controls, limb_ik, limb_ik_fk as match, limb_ik_fk_batch as batch)
import test_body_calibration_blender as setup
import test_body_rest_resync_blender as rest


def action_signature(action):
    if action is None:
        return None
    return (action.as_pointer(), action.name, bool(action.use_fake_user),
            tuple((curve.data_path, curve.array_index, curve.extrapolation, curve.lock, curve.mute,
                   tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                          point.handle_left_type, point.handle_right_type, point.interpolation,
                          point.easing) for point in curve.keyframe_points))
                  for curve in limb_ik._fcurves_for_action(action)))


def complete(rig):
    return {'protected': rest.protected(rig), 'display': bone_display._snapshot(rig),
            'channels': {pb.name: (pb.rotation_mode,
                         tuple((prop, tuple(getattr(pb, prop))) for prop in batch._CHANNELS),
                         tuple((prop, tuple(value) if hasattr(value, '__len__') else value)
                               for prop in batch._LOCKS for value in (getattr(pb, prop),)))
                         for pb in rig.pose.bones},
            'action': action_signature(rig.animation_data.action if rig.animation_data else None),
            'actions': set(action.as_pointer() for action in bpy.data.actions)}


class BodyLimbBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def make(self, *, toes=False, movable_shoulders=False):
        rig, skin = setup.fixture()
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        if toes or movable_shoulders:
            bpy.ops.object.mode_set(mode='EDIT')
            for side in ('L', 'R'):
                if movable_shoulders:
                    rig.data.edit_bones['upper_arm.' + side].use_connect = False
                if toes:
                    foot = rig.data.edit_bones['foot.' + side]
                    toe = rig.data.edit_bones.new('toe.' + side)
                    toe.head = foot.tail
                    toe.tail = toe.head + Vector((0., -.13, 0.))
                    toe.parent = foot
                    toe.use_connect = True
            bpy.ops.object.mode_set(mode='POSE')
        setup.prepare(rig)
        body_setup.generate(bpy.context, rig)
        inventory = limb_ik._validate_inventory(rig)
        self.assertEqual(set(inventory['rigs']), set(batch.LIMB_KEYS))
        self.assertEqual(inventory['schema'], limb_ik.DIRECT_PREROLL_SCHEMA)
        rig.location = (.11, -.06, .18)
        rig.rotation_euler = (.06, -.07, .08)
        rig.scale = (.83,) * 3
        surface = rest.add_surface(rig)
        bpy.context.view_layer.update()
        return rig, skin, surface

    def pose(self, rig):
        match._update(bpy.context, rig)
        return original._pose(rig)

    def assertPose(self, rig, desired):
        errors = match._verify(rig, desired)
        self.assertLessEqual(errors[0], match.POSITION_TOLERANCE)

    def assertSurface(self, rig, before):
        now = rest.world_surfaces(rig)
        self.assertEqual(set(now), set(before))
        for name in before:
            self.assertEqual(len(now[name]), len(before[name]))
            self.assertLess(max((a - b).length for a, b in zip(now[name], before[name])), 4e-4, name)

    def test_four_limb_native_fk_and_auto_align_roundtrip(self):
        rig, _skin, _surface = self.make()
        settings = limb_ik._settings(bpy.context)
        settings.selected_limb = 'RIGHT_ARM'
        limb_ik._set_auto_align_selected_target(bpy.context, rig, settings, enabled=False)
        inventory = limb_ik._validate_inventory(rig)
        for index, key in enumerate(batch.LIMB_KEYS):
            target = rig.pose.bones[inventory['rigs'][key]['target'].name]
            target.location += Vector((.008, -.011, .009))
            target.rotation_euler = (.03, -.02, .015)
        pose, surface = self.pose(rig), rest.world_surfaces(rig)
        geometry = control_pose_assets.native_rest(rig)
        bones = set(rig.data.bones)
        meshes = {obj.name: rest.mesh_raw(obj) for obj in rest.bound_meshes(rig)}
        auto = {key: item['auto_align'] for key, item in inventory['rigs'].items()}
        result = batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(result['changed_keys'], batch.LIMB_KEYS)
        self.assertEqual(batch.mode_for_keys(rig), 'FK')
        self.assertPose(rig, pose)
        self.assertSurface(rig, surface)
        self.assertEqual(set(rig.data.bones), bones, 'No duplicate FK bones may be generated')
        self.assertEqual(control_pose_assets.native_rest(rig), geometry)
        self.assertEqual({obj.name: rest.mesh_raw(obj) for obj in rest.bound_meshes(rig)}, meshes)
        native = rig.pose.bones['upper_arm.L']
        native.rotation_mode = 'XYZ'
        native.rotation_euler.rotate(Euler((.02, .015, -.01)))
        authored = self.pose(rig)
        self.assertGreater(match._rotation_error(pose[native.name], authored[native.name]), .005)
        self.assertEqual(float(rig.pose.bones[inventory['rigs'][('ARM', 'L')]['target'].name]['ik_fk']), 0.)
        result = batch.switch_all(bpy.context, rig, 'IK', keyframe=False)
        self.assertEqual(result['changed_keys'], batch.LIMB_KEYS)
        self.assertPose(rig, authored)
        self.assertEqual(control_pose_assets.native_rest(rig), geometry)
        self.assertEqual({key: item['auto_align'] for key, item in limb_ik._validate_inventory(rig)['rigs'].items()}, auto)

    def test_partial_advanced_mode_keeps_other_limb_inputs(self):
        rig, _skin, _surface = self.make()
        inventory = limb_ik._validate_inventory(rig)
        unaffected = {name: rig.pose.bones[name].matrix_basis.copy()
                      for key in batch.LIMB_KEYS[1:] for name in inventory['rigs'][key]['chain']}
        desired = self.pose(rig)
        result = batch.switch_all(bpy.context, rig, 'FK', keys=(('ARM', 'L'),), keyframe=False)
        self.assertEqual(result['changed_keys'], (('ARM', 'L'),))
        self.assertEqual(batch.mode_for_keys(rig), 'MIXED')
        self.assertEqual({name: rig.pose.bones[name].matrix_basis for name in unaffected}, unaffected)
        self.assertPose(rig, desired)
        same = batch.switch_all(bpy.context, rig, 'FK', keys=(('ARM', 'L'),), keyframe=True)
        self.assertFalse(same['changed'])
        self.assertFalse(same['keyed'])

    def test_late_limb_match_failure_restores_all_modes_and_rna(self):
        rig, _skin, _surface = self.make()
        before = complete(rig)
        match_fk, calls = match._match_fk, []
        def fail_last(*args, **kwargs):
            calls.append(tuple(args[2]['chain']))
            if len(calls) == 4:
                raise ValueError('injected last limb match failure')
            return match_fk(*args, **kwargs)
        with patch.object(match, '_match_fk', side_effect=fail_last):
            with self.assertRaisesRegex(ValueError, 'last limb match'):
                batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(len(calls), 4)
        self.assertEqual(complete(rig), before)
        self.assertEqual(batch.mode_for_keys(rig), 'IK')
        limb_ik._validate_inventory(rig)

    def test_new_switching_drivers_are_removed_on_late_failure(self):
        rig, _skin, _surface = self.make()
        match.remove_switching(rig)
        self.pose(rig)
        before = complete(rig)
        previous_match, calls = match._match_fk, []
        def fail_last(*args, **kwargs):
            calls.append(args[2]['chain'])
            if len(calls) == 4:
                raise ValueError('injected after installing native drivers')
            return previous_match(*args, **kwargs)
        with patch.object(match, '_match_fk', side_effect=fail_last):
            with self.assertRaisesRegex(ValueError, 'installing native drivers'):
                batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(complete(rig), before)
        limb_ik._validate_inventory(rig)

    def test_last_autokey_failure_keeps_shared_artist_action_exact(self):
        rig, _skin, _surface = self.make()
        inventory = limb_ik._validate_inventory(rig)
        first = rig.pose.bones[inventory['rigs'][batch.LIMB_KEYS[0]]['target'].name]
        first.keyframe_insert(data_path='["ik_fk"]', frame=1)
        torso = rig.pose.bones['Hips']
        torso.location.x = .025
        torso.keyframe_insert(data_path='location', frame=1)
        torso.location.x = -.01
        torso.keyframe_insert(data_path='location', frame=20)
        shared = bpy.data.objects.new('Shared Action reader', None)
        bpy.context.scene.collection.objects.link(shared)
        shared.animation_data_create().action = rig.animation_data.action
        bpy.context.scene.frame_set(10)
        match._update(bpy.context, rig)
        before = complete(rig)
        old_action = rig.animation_data.action
        previous_insert = bpy.types.PoseBone.keyframe_insert
        fourth = inventory['rigs'][batch.LIMB_KEYS[-1]]['target'].name
        inserts = []
        def insert(pb, *args, **kwargs):
            path = kwargs.get('data_path', args[0] if args else '')
            if path == '["ik_fk"]' and kwargs.get('frame') == 10:
                inserts.append(pb.name)
                if pb.name == fourth:
                    return False
            return previous_insert(pb, *args, **kwargs)
        bpy.types.PoseBone.keyframe_insert = insert
        try:
            with self.assertRaisesRegex(limb_ik.LimbIKError, 'Could not key'):
                batch.switch_all(bpy.context, rig, 'FK', keyframe=True)
        finally:
            bpy.types.PoseBone.keyframe_insert = previous_insert
        self.assertEqual(len(inserts), 4, 'Three earlier limbs must actually stage successful keys')
        self.assertEqual(rig.animation_data.action, old_action)
        self.assertEqual(shared.animation_data.action, old_action)
        self.assertEqual(complete(rig), before)

    def test_all_autokey_modes_keep_previous_pose_and_unrelated_curve(self):
        rig, _skin, _surface = self.make()
        torso = rig.pose.bones['Hips']
        torso.location.x = .015
        torso.keyframe_insert(data_path='location', frame=1)
        torso.location.x = -.01
        torso.keyframe_insert(data_path='location', frame=20)
        torso_path = torso.path_from_id('location')
        original_curve = next(curve for curve in limb_ik._fcurves_for_action(rig.animation_data.action)
                              if curve.data_path == torso_path and curve.array_index == 0)
        samples = {frame: original_curve.evaluate(frame) for frame in (1., 2.5, 8.25, 9., 9.5, 10., 15., 20.)}
        original_action = rig.animation_data.action
        self.assertFalse(original_action.use_fake_user)
        old_actions = set(action.as_pointer() for action in bpy.data.actions)
        original_pointer = original_action.as_pointer()
        bpy.context.scene.frame_set(10)
        desired = self.pose(rig)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        result = batch.switch_all(bpy.context, rig, 'FK')
        self.assertTrue(result['keyed'])
        self.assertFalse(rig.animation_data.action.use_fake_user)
        self.assertEqual(set(action.as_pointer() for action in bpy.data.actions),
                         old_actions - {original_pointer} | {rig.animation_data.action.as_pointer()})
        bpy.context.scene.frame_set(20)
        batch.switch_all(bpy.context, rig, 'IK')
        inventory = limb_ik._validate_inventory(rig)
        for frame, expected in ((9, 'IK'), (10, 'FK'), (19, 'FK'), (20, 'IK')):
            bpy.context.scene.frame_set(frame)
            self.pose(rig)
            self.assertEqual(batch.mode_for_keys(rig), expected)
            for key in batch.LIMB_KEYS:
                mode_path = match.property_path(rig.pose.bones[inventory['rigs'][key]['target'].name])
                curve = next(curve for curve in limb_ik._fcurves_for_action(rig.animation_data.action)
                             if curve.data_path == mode_path)
                self.assertTrue(all(point.interpolation == 'CONSTANT' for point in curve.keyframe_points))
        current = next(curve for curve in limb_ik._fcurves_for_action(rig.animation_data.action)
                       if curve.data_path == torso_path and curve.array_index == 0)
        for frame, expected in samples.items():
            self.assertAlmostEqual(current.evaluate(frame), expected, places=7)

    def test_fake_user_action_success_and_failure_never_leak_copies(self):
        for fail in (False, True):
            rig, _skin, _surface = self.make()
            inventory = limb_ik._validate_inventory(rig)
            first = rig.pose.bones[inventory['rigs'][batch.LIMB_KEYS[0]]['target'].name]
            first.keyframe_insert(data_path='["ik_fk"]', frame=1)
            source_action = rig.animation_data.action
            source_action.use_fake_user = True
            old_actions = set(action.as_pointer() for action in bpy.data.actions)
            source_signature = action_signature(source_action)
            bpy.context.scene.frame_set(10)
            self.pose(rig)
            before = complete(rig)
            previous_key, calls = match._key_switch, []
            def fail_last(*args, **kwargs):
                calls.append(args[2].name)
                if fail and len(calls) == 4:
                    # Force failure after the fourth copy actually exists.
                    original_insert = bpy.types.PoseBone.keyframe_insert
                    bpy.types.PoseBone.keyframe_insert = lambda *a, **kw: False
                    try:
                        return previous_key(*args, **kwargs)
                    finally:
                        bpy.types.PoseBone.keyframe_insert = original_insert
                return previous_key(*args, **kwargs)
            with patch.object(match, '_key_switch', side_effect=fail_last):
                if fail:
                    with self.assertRaisesRegex(limb_ik.LimbIKError, 'Could not key'):
                        batch.switch_all(bpy.context, rig, 'FK', keyframe=True)
                    self.assertEqual(complete(rig), before)
                else:
                    batch.switch_all(bpy.context, rig, 'FK', keyframe=True)
                    self.assertEqual(set(action.as_pointer() for action in bpy.data.actions),
                                     old_actions | {rig.animation_data.action.as_pointer()})
                    self.assertTrue(rig.animation_data.action.use_fake_user)
            self.assertEqual(len(calls), 4)
            self.assertEqual(action_signature(source_action), source_signature)

    def test_ambiguous_action_slots_fail_before_original_or_pose_writes(self):
        rig, _skin, _surface = self.make()
        target = rig.pose.bones[limb_ik._validate_inventory(rig)['rigs'][('ARM', 'L')]['target'].name]
        target.keyframe_insert(data_path='["ik_fk"]', frame=1)
        action = rig.animation_data.action
        slot = action.slots.new(id_type='OBJECT', name='Unrelated character')
        channelbag = action.layers[0].strips[0].channelbag(slot, ensure=True)
        curve = channelbag.fcurves.new(data_path=match.property_path(target), index=0)
        curve.keyframe_points.insert(1., .25)
        curve.keyframe_points.insert(20., .75)
        original.enter(bpy.context, rig)
        before = complete(rig)
        with self.assertRaisesRegex(limb_ik.LimbIKError, 'single-slot'):
            batch.switch_all(bpy.context, rig, 'FK', keyframe=True)
        self.assertTrue(original.active(rig))
        self.assertEqual(complete(rig), before)

    def test_original_pose_becomes_native_fk_without_changing_mesh(self):
        rig, _skin, _surface = self.make()
        original.enter(bpy.context, rig)
        native = rig.pose.bones['forearm.L']
        native.rotation_mode = 'XYZ'
        native.rotation_euler.rotate(Euler((.025, -.01, .015)))
        pose, surface = self.pose(rig), rest.world_surfaces(rig)
        meshes = {obj.name: rest.mesh_raw(obj) for obj in rest.bound_meshes(rig)}
        geometry = control_pose_assets.native_rest(rig)
        self.assertEqual(batch.mode_for_keys(rig), 'FK')
        result = batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertTrue(result['left_original'])
        self.assertFalse(original.active(rig))
        self.assertEqual(batch.mode_for_keys(rig), 'FK')
        self.assertPose(rig, pose)
        self.assertSurface(rig, surface)
        self.assertEqual(control_pose_assets.native_rest(rig), geometry)
        self.assertEqual({obj.name: rest.mesh_raw(obj) for obj in rest.bound_meshes(rig)}, meshes)

    def test_failure_after_original_leave_restores_exact_original_session(self):
        rig, _skin, _surface = self.make()
        original.enter(bpy.context, rig)
        rig.pose.bones['forearm.R'].rotation_euler.x += .012
        self.pose(rig)
        before, surface = complete(rig), rest.world_surfaces(rig)
        match_fk, calls = match._match_fk, []
        def fail_last(*args, **kwargs):
            if original.active(rig):
                return match_fk(*args, **kwargs)
            calls.append(args[2]['chain'])
            if len(calls) == 4:
                self.assertFalse(original.active(rig), 'Injection must follow a successful Original leave')
                raise ValueError('injected after Original leave')
            return match_fk(*args, **kwargs)
        with patch.object(match, '_match_fk', side_effect=fail_last):
            with self.assertRaisesRegex(ValueError, 'after Original leave'):
                batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertTrue(original.active(rig))
        self.assertEqual(complete(rig), before)
        self.assertSurface(rig, surface)
        self.assertTrue(batch.switch_all(bpy.context, rig, 'FK', keyframe=False)['left_original'])

    def test_pending_rest_adaptation_rolls_back_metadata_then_retries(self):
        rig, _skin, _surface = self.make(movable_shoulders=True)
        original.enter(bpy.context, rig)
        bpy.ops.object.mode_set(mode='EDIT')
        for side, sign in (('L', 1), ('R', -1)):
            rig.data.edit_bones['upper_arm.' + side].head += Vector((sign * .007, 0., -.003))
        rig.data.edit_bones['upper_arm.R'].roll += math.pi
        bpy.ops.object.mode_set(mode='POSE')
        self.pose(rig)
        before = complete(rig)
        geometry = control_pose_assets.native_rest(rig)
        match_fk, calls = match._match_fk, []
        def fail_last(*args, **kwargs):
            if original.active(rig):
                return match_fk(*args, **kwargs)
            calls.append(args[2]['chain'])
            if len(calls) == 4:
                self.assertIn(body_rest_resync.PROVENANCE_KEY, rig)
                raise ValueError('injected after accepted Rest adaptation')
            return match_fk(*args, **kwargs)
        with patch.object(match, '_match_fk', side_effect=fail_last):
            with self.assertRaisesRegex(ValueError, 'accepted Rest adaptation'):
                batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(complete(rig), before)
        self.assertEqual(control_pose_assets.native_rest(rig), geometry)
        self.assertTrue(original.active(rig))
        batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(control_pose_assets.native_rest(rig), geometry)
        self.assertEqual(len(json.loads(rig[body_rest_resync.PROVENANCE_KEY])['events']), 1)

    def test_original_dress_corrections_and_constraint_modes_roll_back(self):
        from character_designer import skirt_rig, skirt_original_mode
        from test_skirt_topology_blender import frustum
        from test_skirt_original_mode_blender import constraints_state
        rig, _skin, _surface = self.make()
        source = frustum('Artist Dress', rows=6, sides=16)
        source.matrix_world = rig.matrix_world
        source.shape_key_add(name='Basis')
        source.shape_key_add(name='Artist asymmetry').data[0].co.x += .012
        record = skirt_rig.build_skirt(bpy.context, source, chain_count=4, segment_count=3, armature=rig)
        limb_ik._mode_set(bpy.context, rig, 'POSE')
        original.enter(bpy.context, rig)
        name = record['chains'][0]['def'][1]
        pose = rig.pose.bones[name]
        pose.rotation_mode = 'XYZ'
        pose.rotation_euler.x += .11
        self.pose(rig)
        before, source_props = complete(rig), rest.properties(source)
        constraints = constraints_state(rig)
        before_dress = skirt_original_mode.capture(bpy.context, rig, json.loads(rig[original.SESSION])['dress_edit'])
        match_fk, calls = match._match_fk, []
        def fail_last(*args, **kwargs):
            if original.active(rig):
                return match_fk(*args, **kwargs)
            calls.append(args[2]['chain'])
            if len(calls) == 4:
                self.assertIn(skirt_original_mode.CORRECTIONS, source,
                              'Original leave must actually commit the authored local Dress correction')
                raise ValueError('injected after Dress correction commit')
            return match_fk(*args, **kwargs)
        with patch.object(match, '_match_fk', side_effect=fail_last):
            with self.assertRaisesRegex(ValueError, 'Dress correction commit'):
                batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(len(calls), 4)
        self.assertEqual(complete(rig), before)
        self.assertEqual(rest.properties(source), source_props)
        self.assertEqual(constraints_state(rig), constraints)
        saved = json.loads(rig[original.SESSION])
        skirt_original_mode.verify(bpy.context, rig, saved['dress_edit'], desired=before_dress)
        batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertIn(skirt_original_mode.CORRECTIONS, source)

    def test_reverse_foot_toes_keep_roll_controls_and_native_pose(self):
        rig, _skin, _surface = self.make(toes=True)
        # Body Setup may already add mapped Toe controls; build only absent ones.
        inventory = limb_ik._validate_inventory(rig)
        for side in ('L', 'R'):
            if not inventory['rigs'][('LEG', side)].get('foot_controls'):
                foot_controls.build(bpy.context, rig, ('LEG', side), toe_name='toe.' + side)
        inventory = limb_ik._validate_inventory(rig)
        rolls = {}
        for side in ('L', 'R'):
            record = inventory['rigs'][('LEG', side)]['foot_controls']
            roll, toe = rig.pose.bones[record['roll']], rig.pose.bones[record['toe_control']]
            roll.rotation_mode = toe.rotation_mode = 'XYZ'
            roll.rotation_euler = (.25, .03, 0.)
            toe.rotation_euler = (.13, -.02, 0.)
            rolls[roll.name] = roll.matrix_basis.copy()
        desired = self.pose(rig)
        batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertPose(rig, desired)
        batch.switch_all(bpy.context, rig, 'IK', keyframe=False)
        self.assertPose(rig, desired)
        self.assertEqual({name: rig.pose.bones[name].matrix_basis for name in rolls}, rolls)

    def test_guards_leave_unrelated_rig_and_preview_untouched(self):
        from character_designer import forearm_twist
        rig, _skin, _surface = self.make()
        before = complete(rig)
        other = bpy.data.objects.new('Other character', bpy.data.armatures.new('Other character data'))
        bpy.context.scene.collection.objects.link(other)
        state = character_setup.settings(bpy.context)
        state.rig = other
        with self.assertRaisesRegex(limb_ik.LimbIKError, 'saved character'):
            batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        state.rig = rig
        forearm_twist._SESSION = {'armature': rig}
        try:
            with self.assertRaisesRegex(limb_ik.LimbIKError, 'Forearm preview'):
                batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        finally:
            forearm_twist._SESSION = None
        self.assertEqual(complete(rig), before)
        sharing = bpy.data.objects.new('Shared armature reader', rig.data)
        bpy.context.scene.collection.objects.link(sharing)
        with self.assertRaisesRegex(limb_ik.LimbIKError, 'shared'):
            batch.switch_all(bpy.context, rig, 'FK', keyframe=False)
        self.assertEqual(complete(rig), before)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BodyLimbBatchTests))
    if not result.wasSuccessful():
        raise RuntimeError('Body limb IK/FK batch tests failed')
