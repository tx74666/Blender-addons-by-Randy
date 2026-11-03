"""Wrist Alt+R adapter/keymap ownership tests; no Blender or native events."""

import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class ID:
    def __init__(self, **fields):
        self.__dict__.update(fields)
        self.properties = {}

    def get(self, name, default=None):
        return self.properties.get(name, default)


class Items:
    def __init__(self):
        self.values = []
        self.new_calls = []
        self.remove_calls = []

    def __iter__(self):
        return iter(self.values)

    def new(self, idname, event_type, value, **flags):
        self.new_calls.append((idname, event_type, value, flags.copy()))
        item = NS(idname=idname, type=event_type, value=value, active=True,
                  flags=flags.copy(), properties=NS())
        self.values.insert(0 if flags.get('head') else len(self.values), item)
        return item

    def remove(self, item):
        self.remove_calls.append(item)
        self.values.remove(item)


class Maps(dict):
    def new(self, *, name, space_type, region_type):
        self[name] = NS(name=name, space_type=space_type,
                        region_type=region_type, keymap_items=Items())
        return self[name]


class WristRotationUI(unittest.TestCase):
    def setUp(self):
        package_name = '_wrist_ui_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = [str(SOURCE)]
        self.backend_name = package_name + '.limb_ik_wrist_rotation'
        self.backend = ModuleType(self.backend_name)
        self.backend.reset_wrist_neutral = Mock()
        self.bpy = ModuleType('bpy')
        types = ModuleType('bpy.types')

        class Operator:
            is_registered = False

            def report(self, kinds, message):
                self.messages = getattr(self, 'messages', []) + [(kinds, message)]

        types.Operator = Operator
        self.bpy.types = types
        self.bpy.ops = NS(pose=NS(rot_clear=Mock()))
        self.bpy.utils = NS(register_class=Mock(side_effect=lambda cls: setattr(cls, 'is_registered', True)),
                            unregister_class=Mock(side_effect=lambda cls: setattr(cls, 'is_registered', False)))
        self.configs = NS(addon=NS(keymaps=Maps()), default=NS(keymaps=Maps()), user=NS(keymaps=Maps()))
        for config in vars(self.configs).values():
            config.keymaps.new(name='Pose', space_type='EMPTY', region_type='WINDOW')
        self.addon_map = self.configs.addon.keymaps['Pose']
        self.foreign = self.addon_map.keymap_items.new('another_addon.rotation', 'R', 'PRESS', alt=True)
        self.stock = self.configs.default.keymaps['Pose'].keymap_items.new('pose.rot_clear', 'R', 'PRESS', alt=True)
        self.user = self.configs.user.keymaps['Pose'].keymap_items.new('user.rotation', 'R', 'PRESS', alt=True)
        self.user.active = False
        self.bpy.context = NS(window_manager=NS(keyconfigs=self.configs))
        self.limb = ModuleType(package_name + '.limb_ik')
        tree = ast.parse((SOURCE / 'limb_ik.py').read_bytes())
        names = {'OWNER_KEY', 'OWNER_VALUE', 'VERSION_KEY', 'RIG_VERSION', 'ARMATURE_ID_KEY', 'ROLE_KEY', 'RIG_ID_KEY'}
        scope = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names:
                scope[node.targets[0].id] = ast.literal_eval(node.value)
        owned = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_owned')
        exec(compile(ast.Module(body=[owned], type_ignores=[]), str(SOURCE / 'limb_ik.py'), 'exec'), scope)
        self.limb.__dict__.update(scope)
        self.limb._owned = Mock(wraps=scope['_owned'])
        self.limb._validate_inventory = Mock(side_effect=AssertionError('Poll must not audit the graph.'))
        package.limb_ik = self.limb
        self.package = package
        self.enterContext(patch.dict(sys.modules, {package_name:package, 'bpy':self.bpy,
            'bpy.types':types, self.limb.__name__:self.limb}))
        name = package_name + '.limb_ik_wrist_rotation_ui'
        spec = importlib.util.spec_from_file_location(name, SOURCE / 'limb_ik_wrist_rotation_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        self.enterContext(patch.dict(sys.modules, {name:self.ui}))
        spec.loader.exec_module(self.ui)
        self.addCleanup(self.ui.unregister)
        self.operator = self.ui.CLASSES[0]
        self.rig = ID(type='ARMATURE', data=ID())
        self.rig.data.properties[self.limb.ARMATURE_ID_KEY] = 'owned-rig'
        self.hand = self.bone('Hand.L')
        self.context = NS(mode='POSE', object=self.rig, selected_pose_bones=[self.hand],
                          active_pose_bone=self.hand, body_page=False)

    def bone(self, name, role='HAND_IK', owner='owned-rig'):
        bone = ID()
        bone.properties.update({self.limb.OWNER_KEY:self.limb.OWNER_VALUE,
            self.limb.VERSION_KEY:self.limb.RIG_VERSION,
            self.limb.ARMATURE_ID_KEY:owner, self.limb.ROLE_KEY:role})
        return NS(name=name, bone=bone, id_data=self.rig)

    def load_backend(self):
        self.enterContext(patch.dict(sys.modules, {self.backend_name:self.backend}))
        self.enterContext(patch.object(self.package, 'limb_ik_wrist_rotation', self.backend, create=True))

    def unrelated_snapshot(self):
        return [(item, item.idname, item.type, item.value, item.active, item.flags.copy())
                for item in (self.foreign, self.stock, self.user)]

    def test_poll_is_cheap_any_owned_hand_and_independent_of_page_or_permissions(self):
        for marker in ('CUSTOM', 'SOURCE', 'ORIGINAL', 'FK', 'BLEND'):
            self.context.permission_marker = marker
            for _ in range(5):
                self.assertTrue(self.operator.poll(self.context))
        self.context.selected_pose_bones = [self.bone('Foot', 'FOOT_IK'), self.hand]
        self.assertTrue(self.operator.poll(self.context))
        self.assertNotIn(self.backend_name, sys.modules)
        self.limb._validate_inventory.assert_not_called()
        self.bpy.ops.pose.rot_clear.assert_not_called()

    def test_nonmanaged_or_nonpose_poll_declines_without_calling_native_itself(self):
        for bones in ([], None, [self.bone('FK', 'ARM_FK')], [self.bone('Foot', 'FOOT_IK')],
                      [self.bone('Foreign', owner='foreign')]):
            with self.subTest(bones=bones):
                self.context.selected_pose_bones = bones
                self.assertFalse(self.operator.poll(self.context))
        self.context.selected_pose_bones = [self.hand]
        for mode in ('OBJECT', 'EDIT_ARMATURE', 'EDIT_MESH'):
            self.context.mode = mode
            self.assertFalse(self.operator.poll(self.context))
        self.bpy.ops.pose.rot_clear.assert_not_called()

    def test_owner_version_and_real_posebone_owner_are_required(self):
        self.hand.bone.properties[self.limb.VERSION_KEY] = -1
        self.assertFalse(self.operator.poll(self.context))
        self.hand.bone.properties[self.limb.VERSION_KEY] = self.limb.RIG_VERSION
        self.rig.data.properties.clear()
        self.assertFalse(self.operator.poll(self.context))
        self.rig.data.properties[self.limb.ARMATURE_ID_KEY] = 'owned-rig'
        self.hand.id_data = ID(type='MESH', data=ID())
        self.assertFalse(self.operator.poll(self.context))

    def test_single_hand_routes_real_context_and_target_once_without_native_clear(self):
        self.load_backend()
        # Selection, rather than a stale active-bone value, identifies the target.
        self.context.active_pose_bone = self.bone('Other', 'ARM_FK')
        operator = self.operator()
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.backend.reset_wrist_neutral.assert_called_once_with(self.context, target=self.hand)
        self.bpy.ops.pose.rot_clear.assert_not_called()
        self.assertIn('UNDO', self.operator.bl_options)

    def test_mixed_and_dual_owned_selection_are_consumed_without_backend_or_fallback(self):
        self.load_backend()
        for selected in ([self.hand, self.bone('FK', 'ARM_FK')], [self.hand, self.bone('Hand.R')]):
            with self.subTest(selected=selected):
                self.context.selected_pose_bones = selected
                self.assertTrue(self.operator.poll(self.context))
                operator = self.operator()
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertIn('only one', operator.messages[-1][1])
        self.backend.reset_wrist_neutral.assert_not_called()
        self.bpy.ops.pose.rot_clear.assert_not_called()

    def test_selection_or_context_changed_after_poll_is_rechecked(self):
        self.load_backend()
        self.assertTrue(self.operator.poll(self.context))
        for selected, mode, rig in (([], 'POSE', self.rig), ([self.hand], 'OBJECT', self.rig),
                                     ([self.hand], 'POSE', ID(type='ARMATURE', data=ID()))):
            self.context.selected_pose_bones, self.context.mode, self.context.object = selected, mode, rig
            self.assertEqual(self.operator().execute(self.context), {'CANCELLED'})
        self.backend.reset_wrist_neutral.assert_not_called()

    def test_backend_source_original_fk_blend_mirror_keyingset_refusals_never_pass_through(self):
        self.load_backend()
        for reason in ('Source is read-only', 'Original is retained', 'FK is not IK',
                       'BLEND is unsupported', 'Mirror editing is unsupported', 'Keying set is unsupported'):
            with self.subTest(reason=reason):
                self.backend.reset_wrist_neutral.side_effect = ValueError(reason)
                self.assertTrue(self.operator.poll(self.context))
                operator = self.operator()
                result = operator.execute(self.context)
                self.assertEqual(result, {'CANCELLED'})
                self.assertNotIn('PASS_THROUGH', result)
                self.assertEqual(operator.messages[-1], ({'ERROR'}, reason))
        self.bpy.ops.pose.rot_clear.assert_not_called()

    def test_register_is_idempotent_and_only_adds_its_owned_pose_alt_r_item(self):
        before = self.unrelated_snapshot()
        self.ui.register()
        self.ui.register()
        self.assertEqual(len(self.ui._KEYMAPS), 1)
        km, item = self.ui._KEYMAPS[0]
        self.assertIs(km, self.addon_map)
        self.assertEqual((item.idname,item.type,item.value), (self.operator.bl_idname,'R','PRESS'))
        self.assertEqual(item.flags, dict(alt=True,shift=False,ctrl=False,oskey=False,any=False,head=True))
        self.assertIs(km.keymap_items.values[0], item)
        self.assertEqual(before, self.unrelated_snapshot())
        self.assertEqual(self.bpy.utils.register_class.call_count, 1)
        self.assertNotIn(self.backend_name, sys.modules)
        self.ui.unregister()
        self.ui.unregister()
        self.assertEqual(self.addon_map.keymap_items.values, [self.foreign])
        self.assertEqual(self.ui._KEYMAPS, [])
        self.assertEqual(before, self.unrelated_snapshot())

    def test_registration_failure_rolls_back_only_new_class_and_keeps_existing_items(self):
        before = self.unrelated_snapshot()
        with patch.object(self.addon_map.keymap_items, 'new', side_effect=RuntimeError('keymap failed')):
            with self.assertRaisesRegex(RuntimeError, 'keymap failed'):
                self.ui.register()
        self.assertFalse(self.operator.is_registered)
        self.assertEqual(self.ui._KEYMAPS, [])
        self.assertEqual(before, self.unrelated_snapshot())
        self.operator.is_registered = True
        with patch.object(self.addon_map.keymap_items, 'new', side_effect=RuntimeError('keymap failed')):
            with self.assertRaises(RuntimeError):
                self.ui.register()
        self.assertTrue(self.operator.is_registered)

    def test_missing_addon_config_can_register_class_then_add_binding_later(self):
        self.configs.addon = None
        self.ui.register()
        self.assertTrue(self.operator.is_registered)
        self.assertEqual(self.ui._KEYMAPS, [])
        self.configs.addon = NS(keymaps=Maps())
        self.ui.register()
        self.assertEqual(len(self.ui._KEYMAPS), 1)
        self.assertEqual(self.configs.addon.keymaps['Pose'].space_type, 'EMPTY')
        self.assertEqual(self.configs.addon.keymaps['Pose'].region_type, 'WINDOW')

    def test_wrong_pose_map_context_refuses_without_changing_foreign_map(self):
        self.addon_map.space_type = 'GRAPH_EDITOR'
        with self.assertRaisesRegex(RuntimeError, 'unexpected editor context'):
            self.ui.register()
        self.assertEqual(self.addon_map.space_type, 'GRAPH_EDITOR')
        self.assertEqual(self.addon_map.keymap_items.values, [self.foreign])
        self.assertFalse(self.operator.is_registered)

    def test_removed_native_item_is_tolerated_and_cleanup_failure_retains_retry_handle(self):
        self.ui.register()
        km,item = self.ui._KEYMAPS[0]
        with patch.object(km.keymap_items, 'remove', side_effect=RuntimeError('remove failed')):
            with self.assertRaisesRegex(RuntimeError, 'remove failed'):
                self.ui.unregister()
        self.assertEqual(self.ui._KEYMAPS, [(km,item)])
        self.assertTrue(self.operator.is_registered)
        km.keymap_items.remove(item)
        self.ui.unregister()
        self.assertEqual(self.ui._KEYMAPS, [])
        self.assertFalse(self.operator.is_registered)


if __name__ == '__main__':
    unittest.main()
