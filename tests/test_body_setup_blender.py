"""One-step generation preserves deformation, artist inputs and atomicity."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_setup, limb_ik, root_control, torso_controls,
    foot_controls, spine_ik_fk, eye_controls, limb_fk_visuals, head_neck_visuals, body_detail_visuals)
import test_body_setup_plan_blender as planning


def native_state(rig, body):
    bpy.context.view_layer.update()
    return {'skin': body_setup._native_skin(rig),
            'rest': {name: torso_controls._state(rig.data.bones[name]) for name in body_setup._native_skin(rig)},
            'ids': (rig.as_pointer(), rig.data.as_pointer(), body.as_pointer(), body.data.as_pointer()),
            'weights': tuple(tuple((group.group, group.weight) for group in vertex.groups) for vertex in body.data.vertices)}


def check_native(test, rig, body, before):
    current = native_state(rig, body)
    body_setup._verify_skin(rig, before['skin'])
    test.assertEqual(current['ids'], before['ids'])
    test.assertEqual(current['weights'], before['weights'])
    for name, state in before['rest'].items():
        test.assertTrue(torso_controls._same_rest(rig.data.bones[name], state), name)


def direct_fixture():
    rig, body = planning.fixture()
    rig.pose.bones['Hips'].matrix_basis = Matrix.Identity(4)
    torso_controls._update(bpy.context, rig)
    planning.base.analyze(rig)
    settings = limb_ik._settings(bpy.context)
    settings.build_method = 'DIRECT_PREROLL'
    assert bpy.ops.character_designer.limb_ik_direct_preroll_check() == {'FINISHED'}
    assert bpy.ops.character_designer.limb_ik_build_all() == {'FINISHED'}
    root = root_control.build(bpy.context, rig)
    assert bpy.ops.character_designer.limb_ik_auto_align_target(action='DISABLE') == {'FINISHED'}
    control = rig.pose.bones[root['master']]
    control.location, control.rotation_euler = (.10, -.05, .03), (.12, -.15, .2)
    control[root_control.SCALE_PROPERTY] = 1.08
    inventory = limb_ik._validate_inventory(rig)
    for (kind, side), data in inventory['rigs'].items():
        target = rig.pose.bones[data['target'].name]
        if kind == 'ARM':
            target.location += Vector((-.025 if side == 'L' else .025, -.045, .035))
            target.rotation_mode = 'XYZ'
            target.rotation_euler = (.24, -.16, .19)
    torso_controls._update(bpy.context, rig)
    return rig, body


class BodySetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def test_generate_all_then_reuse_and_reopen(self):
        rig, body = planning.fixture()
        guides = bpy.context.scene.character_designer_finger_definition
        guides.overlays_enabled = True
        before = native_state(rig, body)
        result = body_setup.generate(bpy.context, rig)
        self.assertFalse(guides.overlays_enabled)
        self.assertEqual(tuple(result['created']), planning.planner.COMPONENT_KEYS)
        check_native(self, rig, body, before)
        stable = planning.states(rig, body)
        guides.overlays_enabled = True
        result = body_setup.generate(bpy.context, rig)
        self.assertFalse(guides.overlays_enabled)
        self.assertEqual(result['created'], [])
        self.assertEqual(tuple(result['reused']), planning.planner.COMPONENT_KEYS)
        self.assertEqual(planning.states(rig, body), stable)
        names = rig.name, body.name
        with tempfile.TemporaryDirectory(prefix='cd-body-setup-') as folder:
            path = str(Path(folder) / 'body.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            rig, body = (bpy.data.objects[name] for name in names)
            self.assertFalse(bpy.context.scene.character_designer_finger_definition.overlays_enabled)
            self.assertFalse(body_setup.plan(bpy.context, rig)['blocked'])
            self.assertEqual(body_setup.generate(bpy.context, rig)['created'], [])
            body_setup._verify_skin(rig, before['skin'])

    def test_artist_native_shapes_are_not_replaced(self):
        rig, body = planning.fixture()
        bpy.ops.object.mode_set(mode='EDIT')
        hand = rig.data.edit_bones['hand.L']
        planning.base.add_bone(rig.data.edit_bones, 'index01.L', hand.tail, hand.tail+Vector((.07, 0, 0)), hand)
        bpy.ops.object.mode_set(mode='POSE')
        names = ('Head', 'upper_arm.L', 'forearm.L', 'shoulder.L', 'index01.L')
        shape = bpy.data.objects.new('Artist native control', bpy.data.meshes.new('Artist native control'))
        for name in names:
            rig.pose.bones[name].custom_shape = shape
            rig.pose.bones[name].custom_shape_translation = (.12, -.04, .07)
        before = native_state(rig, body)
        result = body_setup.generate(bpy.context, rig)
        self.assertIn('HEAD_NECK', [entry['key'] for entry in result['skipped']])
        for name in names:
            self.assertEqual(rig.pose.bones[name].custom_shape, shape)
            self.assertLess((rig.pose.bones[name].custom_shape_translation-Vector((.12, -.04, .07))).length, 1e-7)
        check_native(self, rig, body, before)

    def test_reused_setup_updates_wrist_axes_without_rebuilding(self):
        rig, body = planning.fixture()
        body_setup.generate(bpy.context, rig)
        targets = [rig.pose.bones['CTRL_hand_IK.' + side] for side in 'LR']
        for target in targets:
            target.use_transform_at_custom_shape = False
        before = native_state(rig, body)
        bones = tuple(rig.data.bones.keys())
        result = body_setup.generate(bpy.context, rig)
        self.assertEqual(result['created'], [])
        self.assertEqual(set(result['updated']), {target.name for target in targets})
        self.assertEqual(tuple(rig.data.bones.keys()), bones)
        self.assertTrue(all(target.use_transform_at_custom_shape for target in targets))
        check_native(self, rig, body, before)
        for target in targets:
            target.use_transform_at_custom_shape = False
        original_check = body_setup.body_setup_transaction.assert_original_ids
        calls = []
        def fail_once(checkpoint):
            calls.append(True)
            if len(calls) == 1:
                raise ValueError('injected after axes')
            return original_check(checkpoint)
        with patch.object(body_setup.body_setup_transaction, 'assert_original_ids', side_effect=fail_once):
            bpy.context.scene.character_designer_finger_definition.overlays_enabled = True
            with self.assertRaisesRegex(ValueError, 'injected after axes'):
                body_setup.generate(bpy.context, rig)
        self.assertTrue(bpy.context.scene.character_designer_finger_definition.overlays_enabled)
        self.assertTrue(all(not rig.pose.bones[target.name].use_transform_at_custom_shape for target in targets))
        check_native(self, rig, body, before)

    def test_existing_manual_limbs_and_transformed_root_are_reused(self):
        rig, body = direct_fixture()
        before = native_state(rig, body)
        controls = {pb.name: (pb.matrix_basis.copy(), pb.custom_shape, pb.custom_shape_transform)
                    for pb in rig.pose.bones if pb.bone.get(limb_ik.OWNER_KEY)}
        root_raw = rig.data[root_control.RECORD_KEY]
        result = body_setup.generate(bpy.context, rig)
        self.assertIn('ROOT', result['reused'])
        self.assertIn('LIMBS', result['reused'])
        self.assertEqual(rig.data[root_control.RECORD_KEY], root_raw)
        check_native(self, rig, body, before)
        for name, (basis, shape, transform) in controls.items():
            pb = rig.pose.bones[name]
            self.assertLess(max(abs(pb.matrix_basis[i][j]-basis[i][j]) for i in range(4) for j in range(4)), 1e-6, name)
            self.assertEqual((pb.custom_shape, pb.custom_shape_transform), (shape, transform), name)
        self.assertTrue(all(not data['auto_align'] for data in limb_ik._validate_inventory(rig)['rigs'].values()))

    def test_generate_preserves_posed_arms_and_their_current_elbow_plane(self):
        rig, body = planning.fixture()
        for side, sign in (('L', 1), ('R', -1)):
            for role, angles in (('upper_arm', (.48, 1.15*sign, -.37*sign)),
                                 ('forearm', (.25, -.62*sign, .42*sign)),
                                 ('hand', (.43, .81*sign, -.36*sign))):
                pb = rig.pose.bones[role+'.'+side]
                pb.rotation_mode = 'XYZ'
                pb.rotation_euler = angles
        torso_controls._update(bpy.context, rig)
        before = native_state(rig, body)
        bends = {}
        for side in ('L', 'R'):
            upper, lower = rig.pose.bones['upper_arm.'+side], rig.pose.bones['forearm.'+side]
            bends[side] = limb_ik._project_perpendicular(lower.head-upper.head, lower.tail-upper.head).normalized()
        result = body_setup.generate(bpy.context, rig)
        self.assertIn('LIMBS', result['created'])
        check_native(self, rig, body, before)
        inventory = limb_ik._validate_inventory(rig)
        for side in ('L', 'R'):
            upper, lower = rig.pose.bones['upper_arm.'+side], rig.pose.bones['forearm.'+side]
            pole = rig.pose.bones[inventory['rigs'][('ARM', side)]['pole'].name]
            pole_bend = limb_ik._project_perpendicular(pole.head-upper.head, lower.tail-upper.head).normalized()
            self.assertGreater(pole_bend.dot(bends[side]), .999, side)

    def test_complete_partial_stable_setup_with_a_transformed_master(self):
        rig, body = planning.fixture()
        planning.base.analyze(rig)
        settings = limb_ik._settings(bpy.context)
        settings.build_method, settings.selected_limb = 'ROLL_DECOUPLED', 'LEFT_ARM'
        self.assertEqual(bpy.ops.character_designer.limb_ik_build_selected(), {'FINISHED'})
        master = rig.pose.bones[limb_ik.MASTER_NAME]
        master.rotation_mode = 'XYZ'
        master.location, master.rotation_euler, master.scale = (.11, -.08, .06), (.24, -.35, .46), (1.12,)*3
        rig.pose.bones['forearm.R'].rotation_mode = 'XYZ'
        rig.pose.bones['forearm.R'].rotation_euler = (.21, -.29, .34)
        torso_controls._update(bpy.context, rig)
        before = native_state(rig, body)
        existing = {pb.name: pb.matrix_basis.copy() for pb in rig.pose.bones
                    if pb.bone.get(limb_ik.OWNER_KEY) in limb_ik.GENERATED_CONTROL_OWNERS}
        result = body_setup.generate(bpy.context, rig)
        self.assertIn('LIMBS', result['created'])
        self.assertIn('ROOT', result['reused'])
        check_native(self, rig, body, before)
        self.assertEqual(len(limb_ik._validate_inventory(rig)['rigs']), 4)
        for name, basis in existing.items():
            self.assertLess(max(abs(rig.pose.bones[name].matrix_basis[i][j]-basis[i][j])
                                for i in range(4) for j in range(4)), 1e-6, name)
        stable = planning.states(rig, body)
        self.assertEqual(body_setup.generate(bpy.context, rig)['created'], [])
        self.assertEqual(planning.states(rig, body), stable)

    def test_optional_missing_parts_are_skipped_while_available_parts_work(self):
        rig, body = planning.fixture(eyes=False, neck=False, breasts=(), toes=False)
        before = native_state(rig, body)
        result = body_setup.generate(bpy.context, rig)
        self.assertEqual({entry['key'] for entry in result['skipped']}, {'EYES', 'FEET_L', 'FEET_R'})
        self.assertEqual(set(head_neck_visuals.validate(rig)['bindings']), {'Head'})
        self.assertEqual(set(body_detail_visuals.validate(rig)['names']), {'HIPS'})
        check_native(self, rig, body, before)

    def test_invalid_mapping_is_refused_before_a_transaction_or_any_changes(self):
        rig, body = planning.fixture()
        bpy.context.scene.character_designer_finger_definition.overlays_enabled = True
        planning.character_setup.settings(bpy.context).head_bone = 'Missing saved Head'
        before = planning.states(rig, body)
        with patch.object(body_setup.body_setup_transaction, 'capture', side_effect=AssertionError('Mutation started')):
            with self.assertRaisesRegex(ValueError, 'HEAD_NECK'):
                body_setup.generate(bpy.context, rig)
        self.assertEqual(planning.states(rig, body), before)
        self.assertTrue(bpy.context.scene.character_designer_finger_definition.overlays_enabled)

    def test_existing_shoulder_animation_keeps_driving_its_native_arm(self):
        rig, body = planning.fixture()
        shoulder = rig.pose.bones['shoulder.L']
        shoulder.rotation_mode = 'XYZ'
        shoulder.rotation_euler = (.1, -.2, .3)
        shoulder.keyframe_insert('rotation_euler', frame=1)
        shoulder.rotation_euler = (-.4, .7, -.6)
        shoulder.keyframe_insert('rotation_euler', frame=20)
        action = rig.animation_data.action
        curves = tuple((curve.data_path, curve.array_index,
                        tuple(tuple(key.co) for key in curve.keyframe_points))
                       for curve in limb_ik._fcurves_for_action(action))
        samples = {}
        for frame in (1, 11, 20):
            bpy.context.scene.frame_set(frame)
            torso_controls._update(bpy.context, rig)
            samples[frame] = {name: matrix for name, matrix in body_setup._native_skin(rig).items()
                              if name in {'upper_arm.L', 'forearm.L', 'hand.L'}}
        result = body_setup.generate(bpy.context, rig)
        self.assertIn('LIMBS', result['created'])
        self.assertNotIn(('ARM', 'L'), limb_ik._validate_inventory(rig)['rigs'])
        self.assertEqual(rig.animation_data.action, action)
        self.assertEqual(tuple((curve.data_path, curve.array_index,
                               tuple(tuple(key.co) for key in curve.keyframe_points))
                              for curve in limb_ik._fcurves_for_action(action)), curves)
        for frame, skin in samples.items():
            bpy.context.scene.frame_set(frame)
            torso_controls._update(bpy.context, rig)
            body_setup._verify_skin(rig, skin)

    def test_late_failure_restores_new_and_existing_rigs(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                rig, body = direct_fixture() if existing else planning.fixture()
                bpy.context.scene.character_designer_finger_definition.overlays_enabled = True
                before = native_state(rig, body)
                stable = planning.states(rig, body)
                raw = {key: value for key, value in rig.data.items() if isinstance(value, str)}
                original_add = body_setup._add
                def fail(context, armature, entry):
                    result = original_add(context, armature, entry)
                    if entry['key'] == 'BODY_DETAIL':
                        raise RuntimeError('Injected final component failure')
                    return result
                with patch.object(body_setup, '_add', fail):
                    with self.assertRaisesRegex(RuntimeError, 'Injected final component failure'):
                        body_setup.generate(bpy.context, rig)
                self.assertTrue(bpy.context.scene.character_designer_finger_definition.overlays_enabled)
                check_native(self, rig, body, before)
                after = planning.states(rig, body)
                for field in ('resources', 'data_keys', 'object_keys', 'constraints', 'mode', 'selected'):
                    self.assertEqual(after[field], stable[field], field)
                self.assertEqual({name: set(bones) for name, bones in after['collections'].items()},
                                 {name: set(bones) for name, bones in stable['collections'].items()})
                self.assertEqual({key: value for key, value in rig.data.items() if isinstance(value, str)}, raw)
                self.assertFalse([mesh for mesh in bpy.data.meshes if mesh.name.startswith('__CD_BODY_TRANSACTION__')])
                limb_ik._validate_inventory(rig)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BodySetupTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('BODY_SETUP_PASSED', result.testsRun, flush=True)
