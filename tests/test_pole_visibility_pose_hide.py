"""Pose Pole rays follow native Pose hiding, independently of Edit hiding.

Run with ordinary Python; the actual production visibility reader is extracted
without importing Blender or registering the add-on.
"""
import ast
from copy import deepcopy
import math
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/limb_ik.py'


class Item(dict):
    def __init__(self, **attributes):
        super().__init__()
        self.__dict__.update(attributes)


def production_reader():
    tree = ast.parse(SOURCE.read_text(encoding='utf8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name == '_pole_display_visible']
    assert len(nodes) == 1
    scope = dict(math=math, EPSILON=1e-8, KIND_KEY='kind', SIDE_KEY='side',
                 RIG_ID_KEY='rig', ARMATURE_ID_KEY='armature', SIDES=('L', 'R'),
                 LIMB_SPEC={'LEG': {'target': 'foot.{side}', 'target_role': 'FOOT_IK'}},
                 _control_shape_style=lambda bone, strict: bone.get('style', 'DEFAULT'),
                 _owned=lambda bone, armature, role: bone.get('armature') == armature
                        and bone.get('role') == role)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['_pole_display_visible']


class PolePoseVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.visible = production_reader()
        self.collection = SimpleNamespace(is_visible_effectively=True)
        self.bone = Item(hide=False, collections=(self.collection,))
        self.bone.update(kind='LEG', side='L', rig='limb')
        self.pose = SimpleNamespace(hide=False, custom_shape=SimpleNamespace(name='Pole arrow'),
                                    custom_shape_scale_xyz=(1., 1., 1.))
        target_bone = Item()
        target_bone.update(rig='limb', armature='rig', role='FOOT_IK')
        self.target = Item(bone=target_bone)
        self.target['ik_fk'] = 1.
        self.data = Item(show_bone_custom_shapes=True)
        self.data['armature'] = 'rig'
        self.rig = Item(data=self.data,
                        pose=SimpleNamespace(bones={'foot.L': self.target}))
        self.context = SimpleNamespace(space_data=SimpleNamespace(overlay=SimpleNamespace(
            show_overlays=True, show_bones=True)))

    def read(self):
        return self.visible(self.context, self.rig, self.bone, self.pose)

    def test_edit_hidden_pole_with_visible_pose_still_has_ray(self):
        self.bone.hide = True
        self.assertFalse(self.pose.hide)
        self.assertTrue(self.read())

    def test_pose_hidden_pole_never_leaks_ray_for_either_edit_flag(self):
        self.pose.hide = True
        for edit_hidden in (False, True):
            with self.subTest(edit_hidden=edit_hidden):
                self.bone.hide = edit_hidden
                self.assertFalse(self.read())

    def test_legacy_without_pose_hide_preserves_bone_visibility(self):
        del self.pose.hide
        for edit_hidden in (False, True):
            with self.subTest(legacy_hidden=edit_hidden):
                self.bone.hide = edit_hidden
                self.assertEqual(self.read(), not edit_hidden)

    def test_other_native_visibility_gates_still_hide_edit_hidden_pose(self):
        self.bone.hide = True
        cases = ((self.data, 'show_bone_custom_shapes', False),
                 (self.pose, 'custom_shape', None),
                 (self.pose, 'custom_shape_scale_xyz', (0., 0., 0.)),
                 (self.collection, 'is_visible_effectively', False),
                 (self.context.space_data.overlay, 'show_overlays', False),
                 (self.context.space_data.overlay, 'show_bones', False))
        for owner, name, value in cases:
            with self.subTest(gate=name):
                old = getattr(owner, name)
                setattr(owner, name, value)
                try:
                    self.assertFalse(self.read())
                finally:
                    setattr(owner, name, old)
                self.assertTrue(self.read())

    def test_fk_and_foreign_target_remain_hidden(self):
        self.bone.hide = True
        for value in (0., 1e-7, float('nan'), float('inf'), '1', True):
            with self.subTest(ik_fk=value):
                self.target['ik_fk'] = value
                self.assertFalse(self.read())
        self.target['ik_fk'] = 1.
        self.target.bone['rig'] = 'foreign-limb'
        self.assertFalse(self.read())
        self.target.bone['rig'] = 'limb'
        self.target.bone['armature'] = 'foreign-rig'
        self.assertFalse(self.read())

    def test_display_presets_and_sphere_policy_remain_hidden(self):
        self.bone.hide = True
        for owner, key, value in ((self.rig, 'character_designer_body_original_mode_v1', '{}'),
                                  (self.data, 'character_designer_bone_display_view_v1', '{}'),
                                  (self.bone, 'style', 'SPHERE')):
            with self.subTest(gate=key):
                owner[key] = value
                try:
                    self.assertFalse(self.read())
                finally:
                    del owner[key]
                self.assertTrue(self.read())

    def test_read_does_not_change_display_or_author_bindings(self):
        self.bone.hide = True
        state = lambda: deepcopy((vars(self.context), dict(self.rig), vars(self.rig),
                                   vars(self.bone), dict(self.bone), vars(self.pose)))
        before = state()
        self.assertTrue(self.read())
        self.assertEqual(state(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
