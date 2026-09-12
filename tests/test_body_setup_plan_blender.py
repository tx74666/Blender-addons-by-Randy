"""Read-only Body Setup discovery and reversible optional native displays."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_setup_plan as planner, body_detail_visuals, character_setup,
    eye_controls, foot_controls, head_neck_visuals, limb_fk_visuals, limb_ik, root_control,
    spine_ik_fk, torso_controls)
import test_limb_ik_blender as base


def fixture(*, eyes=True, neck=True, breasts=('L', 'R'), toes=True):
    base.reset_scene()
    rig = base.make_humanoid(roll_offset=.31)
    bpy.ops.object.mode_set(mode='EDIT')
    bones = rig.data.edit_bones
    first = bones['Chest']
    first.name, first.tail = 'spine', (0, 0, 1.28)
    chest = base.add_bone(bones, 'Chest', first.tail, (0, 0, 1.42), first)
    chest = base.add_bone(bones, 'UpperChest', chest.tail, (0, 0, 1.55), chest)
    for side in ('L', 'R'):
        bones['shoulder.' + side].parent = chest
    parent = base.add_bone(bones, 'Neck', chest.tail, (0, 0, 1.67), chest) if neck else chest
    head = base.add_bone(bones, 'Head', (0, 0, 1.67), (0, 0, 1.9), parent)
    for side, sign in (('L', 1), ('R', -1)):
        if eyes:
            base.add_bone(bones, 'eye.' + side, (sign*.035, -.025, 1.79), (sign*.04, -.095, 1.79), head)
        if side in breasts:
            base.add_bone(bones, 'breast.' + side, (sign*.085, -.01, 1.50), (sign*.085, -.12, 1.48), chest)
        if toes:
            foot = bones['foot.' + side]
            base.add_bone(bones, 'toe.' + side, foot.tail, foot.tail + Vector((0, -.13, 0)), foot)
    bpy.ops.object.mode_set(mode='POSE')
    rig.rotation_euler, rig.scale = (.16, -.12, .23), (.83,)*3
    rig.pose.bones['Hips'].rotation_mode = 'XYZ'
    rig.pose.bones['Hips'].rotation_euler = (.05, -.03, .04)
    data = bpy.data.meshes.new('Setup test body')
    data.from_pydata([(0, 0, 1.0), (.085, -.12, 1.5), (-.085, -.12, 1.5), (0, -.1, 1.8)], [], [])
    body = bpy.data.objects.new('Setup test body', data)
    bpy.context.scene.collection.objects.link(body)
    body.modifiers.new('Rig', 'ARMATURE').object = rig
    body.vertex_groups.new(name='Hips').add([0], 1., 'REPLACE')
    body.vertex_groups.new(name='Head').add([3], .9, 'REPLACE')
    setup = character_setup.settings(bpy.context)
    setup.rig, setup.body = rig, body
    setup.hips_bone, setup.head_bone = '', ''
    settings = limb_ik._settings(bpy.context)
    settings.armature = None
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    torso_controls._update(bpy.context, rig)
    return rig, body


def states(rig, body):
    return {'rest': {bone.name: torso_controls._state(bone) for bone in rig.data.bones},
            'basis': {pb.name: tuple(tuple(row) for row in pb.matrix_basis) for pb in rig.pose.bones},
            'pose': {pb.name: tuple(tuple(row) for row in pb.matrix) for pb in rig.pose.bones},
            'weights': tuple(tuple((g.group, g.weight) for g in vertex.groups) for vertex in body.data.vertices),
            'data_keys': tuple(rig.data.keys()), 'object_keys': tuple(rig.keys()),
            'constraints': {pb.name: tuple(pb.constraints.keys()) for pb in rig.pose.bones},
            'collections': {c.name: tuple(c.bones.keys()) for c in rig.data.collections_all},
            'resources': (tuple(bpy.data.objects.keys()), tuple(bpy.data.meshes.keys()), tuple(bpy.data.collections.keys())),
            'mode': bpy.context.mode, 'selected': tuple(obj.name for obj in bpy.context.selected_objects)}


def parts(result):
    return {entry['key']: entry for entry in result['components']}


class BodySetupPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def test_full_plan_is_read_only_and_existing_components_are_reused(self):
        rig, body = fixture()
        before = states(rig, body)
        result = planner.plan(bpy.context, rig)
        self.assertEqual(states(rig, body), before)
        self.assertEqual(tuple(parts(result)), planner.COMPONENT_KEYS)
        self.assertFalse(result['blocked'], result)
        self.assertTrue(all(entry['status'] == 'ADD' for entry in result['components']), result)
        json.dumps(result)
        base.analyze(rig)
        limb_ik._settings(bpy.context).build_method = 'ROLL_DECOUPLED'
        self.assertEqual(bpy.ops.character_designer.limb_ik_build_all(), {'FINISHED'})
        root_control.build(bpy.context, rig)
        for side in ('L', 'R'):
            foot_controls.build(bpy.context, rig, ('LEG', side), toe_name='toe.'+side)
        torso_controls.build(bpy.context, rig)
        spine_ik_fk.build(bpy.context, rig)
        eye_controls.build(bpy.context, rig)
        limb_fk_visuals.build(bpy.context, rig)
        head_neck_visuals.build(bpy.context, rig, allow_partial=True)
        body_detail_visuals.build(bpy.context, rig, allow_partial=True)
        before = states(rig, body)
        result = planner.plan(bpy.context, rig)
        self.assertTrue(all(entry['status'] == 'REUSE' for entry in result['components']), result)
        self.assertEqual(states(rig, body), before)

    def test_missing_optional_anatomy_and_partial_displays_persist_and_remove(self):
        for breasts in ((), ('L',)):
            with self.subTest(breasts=breasts):
                rig, body = fixture(eyes=False, neck=False, breasts=breasts, toes=False)
                result = planner.plan(bpy.context, rig)
                items = parts(result)
                self.assertFalse(result['blocked'], result)
                for key in ('EYES', 'FEET_L', 'FEET_R'):
                    self.assertEqual(items[key]['status'], 'SKIP')
                self.assertEqual(items['HEAD_NECK']['status'], 'ADD')
                self.assertIsNone(items['HEAD_NECK']['kwargs']['neck_name'])
                before = states(rig, body)
                with self.assertRaises(limb_ik.LimbIKError):
                    head_neck_visuals.resolve_bones(bpy.context, rig)
                with self.assertRaises(limb_ik.LimbIKError):
                    body_detail_visuals.resolve_bones(bpy.context, rig)
                head = head_neck_visuals.build(bpy.context, rig, **items['HEAD_NECK']['kwargs'])
                detail = body_detail_visuals.build(bpy.context, rig, **items['BODY_DETAIL']['kwargs'])
                self.assertEqual(set(head['bindings']), {'Head'})
                self.assertEqual(set(detail['names']), {'HIPS'} | {'BREAST_'+side for side in breasts})
                torso_controls._verify_pose(rig, {name: Matrix(matrix) for name, matrix in before['pose'].items()})
                self.assertEqual(states(rig, body)['weights'], before['weights'])
                self.assertEqual(states(rig, body)['rest'], before['rest'])
                self.assertEqual(parts(planner.plan(bpy.context, rig))['HEAD_NECK']['status'], 'REUSE')
                self.assertEqual(parts(planner.plan(bpy.context, rig))['BODY_DETAIL']['status'], 'REUSE')
                names = rig.name, body.name
                with tempfile.TemporaryDirectory(prefix='cd-partial-body-') as folder:
                    path = str(Path(folder) / 'partial.blend')
                    bpy.ops.wm.save_as_mainfile(filepath=path)
                    bpy.ops.wm.open_mainfile(filepath=path)
                    rig, body = (bpy.data.objects[name] for name in names)
                    self.assertEqual(set(head_neck_visuals.validate(rig)['bindings']), {'Head'})
                    self.assertEqual(set(body_detail_visuals.validate(rig)['names']), set(detail['names']))
                    head_neck_visuals.remove(bpy.context, rig)
                    body_detail_visuals.remove(bpy.context, rig)
                    self.assertIsNone(rig.pose.bones['Head'].custom_shape)
                    self.assertIsNone(rig.pose.bones['Hips'].custom_shape)
                    self.assertEqual(states(rig, body)['weights'], before['weights'])
                    self.assertEqual(states(rig, body)['rest'], before['rest'])

    def test_ambiguous_and_stale_mappings_block_without_guessing(self):
        rig, body = fixture()
        bpy.ops.object.mode_set(mode='EDIT')
        bones = rig.data.edit_bones
        base.add_bone(bones, 'left_eye', (.03, -.02, 1.8), (.03, -.08, 1.8), bones['Head'])
        base.add_bone(bones, 'left_breast', (.08, -.01, 1.53), (.08, -.12, 1.53), bones['UpperChest'])
        base.add_bone(bones, 'toe_extra.L', bones['foot.L'].tail, bones['foot.L'].tail+Vector((.02, -.1, 0)), bones['foot.L'])
        bpy.ops.object.mode_set(mode='POSE')
        before = states(rig, body)
        result = planner.plan(bpy.context, rig)
        self.assertTrue(result['blocked'])
        for key in ('EYES', 'BODY_DETAIL', 'FEET_L'):
            self.assertIn(parts(result)[key]['status'], {'NEEDS_MAPPING', 'BLOCKED'})
        self.assertEqual(states(rig, body), before)
        character_setup.settings(bpy.context).head_bone = 'Missing saved Head'
        self.assertEqual(parts(planner.plan(bpy.context, rig))['HEAD_NECK']['status'], 'NEEDS_MAPPING')

    def test_artist_displays_are_kept_and_existing_limbs_only_plan_missing_sides(self):
        rig, body = fixture()
        mesh = bpy.data.meshes.new('Artist Head Mesh')
        shape = bpy.data.objects.new('Artist Head', mesh)
        rig.pose.bones['Head'].custom_shape = shape
        before = states(rig, body)
        self.assertEqual(parts(planner.plan(bpy.context, rig))['HEAD_NECK']['status'], 'SKIP')
        self.assertEqual(states(rig, body), before)
        rig.pose.bones['Hips'].matrix_basis = Matrix.Identity(4)
        torso_controls._update(bpy.context, rig)
        base.analyze(rig)
        settings = limb_ik._settings(bpy.context)
        settings.build_method, settings.selected_limb = 'DIRECT_PREROLL', 'LEFT_ARM'
        bpy.ops.character_designer.limb_ik_direct_preroll_check()
        self.assertEqual(bpy.ops.character_designer.limb_ik_build_selected(), {'FINISHED'})
        before = states(rig, body)
        result = planner.plan(bpy.context, rig)
        entry = parts(result)['LIMBS']
        self.assertEqual(result['schema'], limb_ik.DIRECT_PREROLL_SCHEMA)
        self.assertEqual(entry['status'], 'ADD')
        self.assertEqual(len(entry['kwargs']['chains']), 3)
        self.assertNotIn(('ARM', 'L'), [(chain['kind'], chain['side']) for chain in entry['kwargs']['chains']])
        self.assertEqual(states(rig, body), before)
        rig.pose.bones['Hips'].rotation_euler = (.07, -.03, .02)
        torso_controls._update(bpy.context, rig)
        before = states(rig, body)
        entry = parts(planner.plan(bpy.context, rig))['LIMBS']
        self.assertEqual(entry['status'], 'BLOCKED', entry)
        self.assertIn('Rest pose', entry['reason'])
        self.assertEqual(states(rig, body), before)
        rig.pose.bones['Hips'].matrix_basis = Matrix.Identity(4)
        torso_controls._update(bpy.context, rig)
        root_control.build(bpy.context, rig)
        before = states(rig, body)
        entry = parts(planner.plan(bpy.context, rig))['LIMBS']
        self.assertEqual(entry['status'], 'BLOCKED', entry)
        self.assertIn('Direct Root', entry['reason'])
        self.assertEqual(states(rig, body), before)

    def test_limb_geometry_and_name_conflicts_are_refused_before_generation(self):
        for conflict in ('STRAIGHT', 'FOREIGN_TARGET'):
            with self.subTest(conflict=conflict):
                rig, body = fixture()
                bpy.ops.object.mode_set(mode='EDIT')
                bones = rig.data.edit_bones
                if conflict == 'STRAIGHT':
                    upper, lower = bones['upper_arm.L'], bones['forearm.L']
                    joint = (upper.head + lower.tail) * .5
                    upper.tail, lower.head = joint, joint
                else:
                    base.add_bone(bones, 'CTRL_hand_IK.L', (0, 0, 2), (0, 0, 2.1), deform=False)
                bpy.ops.object.mode_set(mode='POSE')
                torso_controls._update(bpy.context, rig)
                before = states(rig, body)
                result = planner.plan(bpy.context, rig)
                entry = parts(result)['LIMBS']
                self.assertTrue(result['blocked'], result)
                self.assertEqual(entry['status'], 'BLOCKED', entry)
                self.assertIn('straight' if conflict == 'STRAIGHT' else 'already exists', entry['reason'])
                self.assertEqual(states(rig, body), before)

    def test_authored_native_inputs_skip_only_the_affected_components(self):
        rig, body = fixture()
        hand = rig.pose.bones['hand.L']
        hand.rotation_mode = 'XYZ'
        hand.rotation_euler = (.2, -.1, .3)
        hand.keyframe_insert('rotation_euler', frame=1)
        hand.keyframe_insert('rotation_euler', frame=12)
        eye_constraint = rig.pose.bones['eye.L'].constraints.new('LIMIT_ROTATION')
        eye_constraint.name = 'Artist eye limit'
        toe_constraint = rig.pose.bones['toe.R'].constraints.new('LIMIT_ROTATION')
        toe_constraint.name = 'Artist toe limit'
        torso_controls._update(bpy.context, rig)
        before = states(rig, body)
        result = planner.plan(bpy.context, rig)
        items = parts(result)
        self.assertFalse(result['blocked'], result)
        self.assertEqual(items['LIMBS']['status'], 'ADD')
        self.assertEqual({(chain['kind'], chain['side']) for chain in items['LIMBS']['kwargs']['chains']},
                         {('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R')})
        self.assertIn('animation', items['LIMBS']['reason'])
        for key in ('EYES', 'FEET_R'):
            self.assertEqual(items[key]['status'], 'SKIP')
            self.assertIn('constraints', items[key]['reason'])
        self.assertEqual(items['FEET_L']['status'], 'ADD')
        self.assertEqual(states(rig, body), before)

    def test_ancestor_transform_animation_is_not_frozen_into_new_ik(self):
        for name, keyed, expected in (('shoulder.L', True, {('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R')}),
                                      ('Hips', True, set()), ('shoulder.L', False, {('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R')})):
            with self.subTest(name=name, keyed=keyed):
                rig, body = fixture()
                parent = rig.pose.bones[name]
                parent.rotation_mode = 'XYZ'
                if keyed:
                    parent.keyframe_insert('rotation_euler', frame=1)
                    parent.rotation_euler.y += .45
                    parent.keyframe_insert('rotation_euler', frame=20)
                else:
                    parent.driver_add('rotation_euler', 1).driver.expression = '0.2'
                torso_controls._update(bpy.context, rig)
                before = states(rig, body)
                result = planner.plan(bpy.context, rig)
                entry = parts(result)['LIMBS']
                self.assertFalse(result['blocked'], result)
                self.assertEqual({(chain['kind'], chain['side']) for chain in entry['kwargs']['chains']}, expected)
                self.assertIn('ancestor transform animation', entry['reason'])
                self.assertEqual(states(rig, body), before)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BodySetupPlanTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('BODY_SETUP_PLAN_PASSED', result.testsRun, flush=True)
