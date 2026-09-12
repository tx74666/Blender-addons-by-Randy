"""Public legacy-wrist correction: visibility, preservation and guarded refusal."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import body_controls_ui as ui, limb_ik, limb_ik_fk, root_control
import test_wrist_ik_fk_blender as fixtures

OPERATOR = 'character_designer.upgrade_wrist_rotation'


class Layout:
    def __init__(self, buttons=None):
        self.buttons = [] if buttons is None else buttons
        self.alert = False

    def row(self, **_kwargs):
        return Layout(self.buttons)

    def label(self, **_kwargs):
        pass

    def operator(self, identifier, **kwargs):
        self.buttons.append((identifier, kwargs.get('text'), self.alert))
        return SimpleNamespace()


def correction_buttons():
    layout = Layout()
    ui.draw_fk_visuals(layout, bpy.context)
    return [button for button in layout.buttons if button[0] == OPERATOR]


def legacy_fixture(method, auto):
    build_plan = limb_ik._build_plan
    def legacy_plan(*args, **kwargs):
        plan = build_plan(*args, **kwargs)
        plan.auto_rotation_space, plan.auto_align = 'LOCAL', auto
        return plan
    limb_ik._build_plan = legacy_plan
    try:
        rig, key, data = fixtures.base.build(method, 'LEFT_ARM')
    finally:
        limb_ik._build_plan = build_plan
    record = root_control.build(bpy.context, rig)
    master = rig.pose.bones[record['master']]
    master.rotation_mode = 'XYZ'
    master.location, master.rotation_euler = (.13, -.06, .09), (.21, -.17, .32)
    if root_control.SCALE_PROPERTY in master:
        master[root_control.SCALE_PROPERTY] = 1.17
    else:
        master.scale = (1.17,) * 3
    rig.rotation_euler, rig.scale = (.19, -.11, .27), (.83,) * 3
    data = limb_ik._validate_inventory(rig)['rigs'][key]
    target = rig.pose.bones[data['target'].name]
    target.location += Vector((-.035, -.095, .09))
    target.rotation_mode, target.rotation_euler = 'XYZ', (.31, -.27, .42)
    fixtures.update(rig)
    return rig, key, data


def display_matrix(target):
    return (target.custom_shape_transform or target).matrix @ limb_ik._custom_shape_state_matrix(
        limb_ik._control_visual_state(target))


class WristRotationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def test_public_correction_preserves_pose_and_disappears_after_upgrade(self):
        self.assertIn('UNDO', ui.CHARACTERDESIGNER_OT_upgrade_wrist_rotation.bl_options)
        for method in ('DIRECT_PREROLL', 'ROLL_DECOUPLED'):
            for auto in (True, False):
                with self.subTest(method=method, auto=auto):
                    rig, key, data = legacy_fixture(method, auto)
                    self.assertEqual(correction_buttons(), [(OPERATOR, 'Correct Wrist Rotation', False)])
                    desired = fixtures.native(rig)
                    rest = {bone.name: bone.matrix_local.copy() for bone in rig.data.bones}
                    target = rig.pose.bones[data['target'].name]
                    shape, display = target.custom_shape, display_matrix(target)
                    self.assertTrue(ui.CHARACTERDESIGNER_OT_upgrade_wrist_rotation.poll(bpy.context))
                    # INVOKE must complete directly, without a confirmation dialog.
                    self.assertEqual(bpy.ops.character_designer.upgrade_wrist_rotation('INVOKE_DEFAULT'), {'FINISHED'})
                    limb_ik_fk._verify(rig, desired)
                    self.assertTrue(all(rig.data.bones[name].matrix_local == matrix for name, matrix in rest.items()))
                    self.assertIs(target.custom_shape, shape)
                    actual_display = display_matrix(target)
                    self.assertLess(max(abs(actual_display[i][j] - display[i][j])
                                        for i in range(4) for j in range(4)), 2e-5)
                    self.assertEqual(limb_ik._validate_inventory(rig)['rigs'][key]['auto_rotation_space'], 'PARENT_DELTA')
                    self.assertEqual(correction_buttons(), [])
                    names = set(rig.data.bones.keys())
                    self.assertEqual(bpy.ops.character_designer.upgrade_wrist_rotation(), {'FINISHED'})
                    self.assertEqual(set(rig.data.bones.keys()), names)
                    limb_ik_fk._verify(rig, desired)

    def test_animated_legacy_wrist_refuses_without_changing_pose_or_action(self):
        rig, key, data, _root = fixtures.fixture('DIRECT_PREROLL', 'R', rotation_space='LOCAL')
        target = rig.pose.bones[data['target'].name]
        target.keyframe_insert('rotation_euler', frame=1)
        desired = fixtures.native(rig)
        action = rig.animation_data.action
        curves = [(curve.data_path, curve.array_index, tuple(tuple(point.co) for point in curve.keyframe_points))
                  for curve in limb_ik._fcurves_for_action(action)]
        names = set(rig.data.bones.keys())
        registry = rig.pose.bones[data['chain'][2]][limb_ik.CONSTRAINT_REGISTRY_KEY]
        self.assertEqual(bpy.ops.character_designer.upgrade_wrist_rotation(), {'CANCELLED'})
        self.assertIs(rig.animation_data.action, action)
        self.assertEqual([(curve.data_path, curve.array_index, tuple(tuple(point.co) for point in curve.keyframe_points))
                          for curve in limb_ik._fcurves_for_action(action)], curves)
        self.assertEqual(set(rig.data.bones.keys()), names)
        self.assertEqual(rig.pose.bones[data['chain'][2]][limb_ik.CONSTRAINT_REGISTRY_KEY], registry)
        limb_ik_fk._verify(rig, desired)
        self.assertEqual(correction_buttons(), [(OPERATOR, 'Correct Wrist Rotation', False)])

    def test_button_ignores_unowned_constraints_and_poll_rejects_edit_mode(self):
        rig, _key, _data, _root = fixtures.fixture('DIRECT_PREROLL', 'L')
        rig.pose.bones['hand.R'].constraints.new('COPY_ROTATION').name = 'AUTO_OFFSET_ROTATION'
        self.assertEqual(correction_buttons(), [])
        bpy.ops.object.mode_set(mode='OBJECT')
        self.assertTrue(ui.CHARACTERDESIGNER_OT_upgrade_wrist_rotation.poll(bpy.context))
        bpy.ops.object.mode_set(mode='EDIT')
        self.assertFalse(ui.CHARACTERDESIGNER_OT_upgrade_wrist_rotation.poll(bpy.context))
        bpy.ops.object.mode_set(mode='POSE')

    def test_undo_restores_legacy_graph_and_redo_restores_correction(self):
        rig, key, _data = legacy_fixture('DIRECT_PREROLL', True)
        name = rig.name
        original_names = set(rig.data.bones.keys())
        desired = fixtures.native(rig)
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message='Before wrist correction')
        self.assertEqual(bpy.ops.character_designer.upgrade_wrist_rotation(), {'FINISHED'})
        # Background Python calls do not push the interactive operator boundary.
        bpy.ops.ed.undo_push(message='After wrist correction')
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        rig = bpy.data.objects[name]
        self.assertEqual(set(rig.data.bones.keys()), original_names)
        self.assertEqual(limb_ik._validate_inventory(rig)['rigs'][key]['auto_rotation_space'], 'LOCAL')
        fixtures.update(rig)
        limb_ik_fk._verify(rig, desired)
        self.assertEqual(correction_buttons(), [(OPERATOR, 'Correct Wrist Rotation', False)])
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        rig = bpy.data.objects[name]
        self.assertEqual(limb_ik._validate_inventory(rig)['rigs'][key]['auto_rotation_space'], 'PARENT_DELTA')
        fixtures.update(rig)
        limb_ik_fk._verify(rig, desired)
        self.assertEqual(correction_buttons(), [])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WristRotationUITests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('WRIST_ROTATION_UI_PASSED', result.testsRun, flush=True)
