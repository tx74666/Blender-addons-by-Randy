"""Actual tool selection uses per-object PoseBone state in modern Blender."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


session = _load_tool('composition_session_acceptance')
product_tree = ast.parse((ROOT / 'addons/character_designer/animation_composition.py').read_text(encoding='utf-8'))
owner_node = next(node for node in product_tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == '_pose_selection_owner')
owner_namespace = {}
exec(compile(ast.Module(body=[owner_node], type_ignores=[]), '<actual product selection helper>', 'exec'), owner_namespace)
backend = NS(_pose_selection_owner=owner_namespace['_pose_selection_owner'])


class BoneTable(dict):
    def __init__(self, rows, on_active=None):
        super().__init__((row.name, row) for row in rows)
        self._active, self.on_active = None, on_active

    def __iter__(self):
        return iter(self.values())

    @property
    def active(self):
        return self._active

    @active.setter
    def active(self, bone):
        self._active = bone
        if self.on_active:
            self.on_active(bone)


class ToolPoseSelectionTests(unittest.TestCase):
    def _fixture(self, modern=True, unknown=False, failed_write=False):
        bones = [NS(name=name, hide=False, hide_select=False) for name in ('A', 'B')]
        if modern:
            # Separate Rig objects can share Bone data without sharing Pose selection.
            for bone in bones:
                bone.select = True
            poses = [NS(name=bone.name, bone=bone, select=bone.name == 'B') for bone in bones]
        else:
            for bone in bones:
                bone.select = bone.name == 'B'
            poses = [NS(name=bone.name, bone=bone) for bone in bones]
        if unknown:
            del poses[1].select
            del bones[1].select
        if failed_write:
            class ReadOnlySelect:
                name, bone = poses[0].name, poses[0].bone

                @property
                def select(self):
                    return False

                @select.setter
                def select(self, value):
                    raise RuntimeError('actual Pose selection write failed')
            poses[0] = ReadOnlySelect()
        data = BoneTable(bones, lambda active: setattr(backend._pose_selection_owner(poses[1]), 'select', True))
        data._active = bones[1]
        rig = NS(data=NS(bones=data), pose=NS(bones=BoneTable(poses)), mode='POSE',
                 select_set=lambda value: None)
        context = NS(view_layer=NS(objects=NS(active=None)))
        item, scope = NS(rig=rig), NS(input_by_native={'nativeA': 'A', 'nativeB': 'B'})
        other_rig_poses = [NS(name=bone.name, bone=bone, select=bone.name == 'B') for bone in bones]
        return context, item, scope, poses, bones, other_rig_poses

    def _show(self, context, item, scope, **kwargs):
        with patch.dict(sys.modules, {'bpy': NS()}):
            session._show_control(context, item, scope, **kwargs)

    def test_real_show_control_selects_exact_pose_without_touching_shared_bones_or_other_rig(self):
        context, item, scope, poses, bones, others = self._fixture()
        self._show(context, item, scope, backend=backend)
        self.assertEqual([pb.name for pb in poses if pb.select], ['A'])
        self.assertIs(item.rig.data.bones.active, bones[0])
        self.assertEqual([bone.select for bone in bones], [True, True])
        self.assertEqual([pb.select for pb in others], [False, True])

    def test_real_legacy_bone_api_preserves_exact_active_then_final_flags(self):
        context, item, scope, _poses, bones, _others = self._fixture(modern=False)
        self._show(context, item, scope, backend=backend)
        self.assertEqual([bone.name for bone in bones if bone.select], ['A'])
        self.assertIs(item.rig.data.bones.active, bones[0])

    def test_existing_three_argument_raise_caller_resolves_official_product_helper(self):
        context, item, scope, poses, _bones, _others = self._fixture()
        with patch.object(session.importlib, 'import_module', return_value=backend) as imported:
            self._show(context, item, scope)
        imported.assert_called_once_with('character_designer.animation_composition')
        self.assertEqual([pb.name for pb in poses if pb.select], ['A'])

    def test_unknown_selection_api_fails_before_native_selection_or_active_mutation(self):
        context, item, scope, poses, bones, _others = self._fixture(unknown=True)
        with self.assertRaises(AttributeError):
            self._show(context, item, scope, backend=backend)
        self.assertIs(item.rig.data.bones.active, bones[1])
        self.assertFalse(poses[0].select)
        self.assertFalse(hasattr(bones[1], 'select'))
        self.assertIsNone(context.view_layer.objects.active)

    def test_actual_pose_write_failure_propagates_without_legacy_bone_fallback(self):
        context, item, scope, _poses, bones, _others = self._fixture(failed_write=True)
        with self.assertRaisesRegex(RuntimeError, 'actual Pose selection write failed'):
            self._show(context, item, scope, backend=backend)
        self.assertEqual([bone.select for bone in bones], [True, True])

    def test_pose_visibility_overrides_edit_bone_hide_without_changing_either(self):
        for pose_hidden, edit_hidden in ((True, False), (False, True)):
            with self.subTest(pose_hidden=pose_hidden):
                pb = NS(hide=pose_hidden, bone=NS(hide=edit_hidden))
                self.assertIs(session._pose_hidden(pb), pose_hidden)
                self.assertIs(pb.hide, pose_hidden)
                self.assertIs(pb.bone.hide, edit_hidden)
        self.assertIs(session._pose_hidden(NS(bone=NS(hide=True))), True)

    def test_unknown_or_invalid_visibility_fails_without_manufacturing_hidden_state(self):
        pb = NS(bone=NS())
        with self.assertRaises(AttributeError):
            session._pose_hidden(pb)
        self.assertFalse(hasattr(pb, 'hide'))
        self.assertFalse(hasattr(pb.bone, 'hide'))
        with self.assertRaisesRegex(AssertionError, 'visibility API'):
            session._pose_hidden(NS(hide=None, bone=NS(hide=False)))

    def test_actual_show_control_rejects_hidden_pose_even_when_edit_bone_is_visible(self):
        context, item, scope, poses, bones, _others = self._fixture()
        for pb in poses:
            pb.hide = True
        with self.assertRaisesRegex(AssertionError, 'No actual visible allowed B control'):
            self._show(context, item, scope, backend=backend)
        self.assertIs(item.rig.data.bones.active, bones[1])
        self.assertEqual([pb.select for pb in poses], [False, True])
        self.assertEqual([bone.hide for bone in bones], [False, False])

    def test_actual_raise_candidate_uses_pose_visibility_without_edit_hidden_writes(self):
        source = ROOT / 'tools/composition_raise_authoring.py'
        node = next(node for node in ast.parse(source.read_text(encoding='utf-8')).body
                    if isinstance(node, ast.FunctionDef) and node.name == '_rotation_control')
        namespace = {'timing': NS(require=session._require)}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        poses = [NS(name=name, hide=hidden, bone=NS(hide=not hidden, hide_select=False),
                    lock_rotation=(False, False, False), rotation_mode='XYZ',
                    path_from_id=lambda name=name: 'pose.bones["' + name + '"]')
                 for name, hidden in (('A', True), ('B', False))]
        rig = NS(pose=NS(bones=BoneTable(poses)))
        scope = NS(input_by_native={'nativeA':'A', 'nativeB':'B'},
                   channels=[(pb.path_from_id() + '.rotation_euler', 0) for pb in poses])
        with patch.dict(sys.modules, {'composition_session_acceptance':session}):
            self.assertEqual(namespace['_rotation_control'](rig, scope, None), 'B')
            with self.assertRaisesRegex(AssertionError, 'No exact visible declared FK'):
                namespace['_rotation_control'](rig, scope, 'A')
        self.assertEqual([(pb.hide, pb.bone.hide) for pb in poses], [(True, False), (False, True)])


if __name__ == '__main__':
    unittest.main()
