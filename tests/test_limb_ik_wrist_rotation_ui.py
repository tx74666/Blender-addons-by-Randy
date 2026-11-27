"""Explicit wrist commands and ownership of Blender's Pose keymap."""

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
        package.natural_bind_mode = NS(register=Mock(), unregister=Mock())
        self.backend_name = package_name + '.limb_ik_wrist_rotation'
        self.backend = ModuleType(self.backend_name)
        self.backend.reset_wrist_neutral = Mock()
        self.bpy = ModuleType('bpy')
        types = ModuleType('bpy.types')
        props = ModuleType('bpy.props')
        props.EnumProperty = lambda **kwargs: kwargs

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
            'bpy.types':types, 'bpy.props':props, self.limb.__name__:self.limb}))
        name = package_name + '.limb_ik_wrist_rotation_ui'
        spec = importlib.util.spec_from_file_location(name, SOURCE / 'limb_ik_wrist_rotation_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        self.enterContext(patch.dict(sys.modules, {name:self.ui}))
        spec.loader.exec_module(self.ui)
        self.addCleanup(self.ui.unregister)
        self.operator = self.ui.CLASSES[0]
        self.operator.reference = self.operator.__annotations__['reference']['default']
        self.rig = ID(type='ARMATURE', data=ID())
        self.rig.data.properties[self.limb.ARMATURE_ID_KEY] = 'owned-rig'
        self.hand = self.bone('Hand.L')
        self.context = NS(mode='POSE', object=self.rig, selected_pose_bones=[self.hand],
                          scene=NS(tool_settings=NS(use_keyframe_insert_auto=False)),
                          active_pose_bone=self.hand, body_page=False)

    def bone(self, name, role='HAND_IK', owner='owned-rig'):
        bone = ID()
        bone.properties.update({self.limb.OWNER_KEY:self.limb.OWNER_VALUE,
            self.limb.VERSION_KEY:self.limb.RIG_VERSION,
            self.limb.ARMATURE_ID_KEY:owner, self.limb.ROLE_KEY:role})
        return ID(name=name, bone=bone, id_data=self.rig, rotation_mode='XYZ',
                  lock_rotation=(False,) * 3, lock_rotations_4d=False, lock_rotation_w=False)

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

    def test_shortcut_keeps_partial_rotation_locks_and_fk_on_the_native_path(self):
        self.hand.lock_rotation_w = True
        self.assertTrue(self.operator.poll(self.context))  # W is unused in XYZ.
        self.hand.rotation_mode = 'QUATERNION'
        self.hand.lock_rotations_4d = True
        self.assertFalse(self.operator.poll(self.context))
        self.hand.lock_rotation_w = False
        self.hand.lock_rotation = (False, True, False)
        self.assertFalse(self.operator.poll(self.context))
        self.hand.lock_rotation = (False,) * 3
        self.hand.properties['ik_fk'] = 0.
        self.assertFalse(self.operator.poll(self.context))
        self.hand.properties['ik_fk'] = .96
        self.assertTrue(self.operator.poll(self.context))

    def test_autokey_mixed_available_rotation_uses_compensation_and_custom_key_set_falls_through(self):
        self.context.selected_pose_bones.append(self.bone('Root', 'MASTER'))
        self.context.scene.tool_settings = NS(use_keyframe_insert_auto=True, auto_keying_mode='ADD_REPLACE_KEYS',
            use_keyframe_insert_keyingset=False, use_keyframe_cycle_aware=False)
        self.context.scene.keying_sets = NS(active=None)
        self.context.preferences = NS(edit=NS(use_visual_keying=False, use_keyframe_insert_available=True))
        self.rig.animation_data = NS(action=object())
        self.assertTrue(self.operator.poll(self.context))
        self.context.scene.keying_sets.active = object()
        self.assertFalse(self.operator.poll(self.context))

    def test_single_hand_routes_real_context_and_target_once_without_native_clear(self):
        self.load_backend()
        # Selection, rather than a stale active-bone value, identifies the target.
        self.context.active_pose_bone = self.bone('Other', 'ARM_FK')
        operator = self.operator()
        self.ui.register()
        operator.reference = self.ui._KEYMAPS[0][1].properties.reference
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.backend.reset_wrist_neutral.assert_called_once_with(self.context, target=self.hand, follow_forearm=True)
        self.bpy.ops.pose.rot_clear.assert_not_called()
        self.assertIn('UNDO', self.operator.bl_options)

    def test_mixed_controls_route_to_group_reset_while_foreign_rigs_refuse(self):
        self.load_backend()
        companion = self.bone('FK', 'ARM_FK')
        self.context.selected_pose_bones = [companion, self.hand]
        mixed = NS(reset_mixed_rotation=Mock())
        with patch.object(self.package, 'controller_transform_clear', mixed, create=True):
            self.assertTrue(self.operator.poll(self.context))
            self.assertEqual(self.operator().execute(self.context), {'FINISHED'})
            mixed.reset_mixed_rotation.assert_called_once_with(self.context, (self.hand,), (companion,))
            companion.id_data = ID(type='ARMATURE', data=ID())
            operator = self.operator()
            self.assertEqual(operator.execute(self.context), {'CANCELLED'})
            self.assertIn('same rig', operator.messages[-1][1])
            self.assertEqual(mixed.reset_mixed_rotation.call_count, 1)
        self.backend.reset_wrist_neutral.assert_not_called()
        self.bpy.ops.pose.rot_clear.assert_not_called()

    def test_both_owned_wrists_use_existing_paired_backend(self):
        self.load_backend()
        self.context.selected_pose_bones = [self.hand, self.bone('Hand.R')]
        self.assertEqual(self.operator().execute(self.context), {'FINISHED'})
        self.backend.reset_wrist_neutral.assert_called_once_with(
            self.context, targets=tuple(self.context.selected_pose_bones), follow_forearm=False)
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

    def test_registration_adds_only_one_owned_alt_r_and_teardown_restores_all_maps(self):
        maps = [config.keymaps['Pose'] for config in vars(self.configs).values()]
        for km in maps:
            for key, op in [('G', 'pose.loc_clear'), ('R', 'pose.rot_clear'), ('S', 'pose.scale_clear')]:
                km.keymap_items.new(op, key, 'PRESS', alt=True)
        before = [list(km.keymap_items) for km in maps]
        unrelated = self.unrelated_snapshot()
        self.ui.register()
        self.ui.register()
        self.assertEqual(self.bpy.utils.register_class.call_count, len(self.ui.CLASSES))
        self.assertNotIn(self.backend_name, sys.modules)
        owned = self.ui._KEYMAPS[0][1]
        self.assertEqual((owned.idname, owned.type, owned.value, owned.flags),
                         (self.operator.bl_idname, 'R', 'PRESS', {'alt': True, 'head': True}))
        self.assertEqual([list(km.keymap_items) for km in maps], [[owned, *before[0]], *before[1:]])
        self.ui.unregister()
        self.ui.unregister()
        self.assertEqual([list(km.keymap_items) for km in maps], before)
        self.assertEqual(self.unrelated_snapshot(), unrelated)

    def test_no_key_configuration_is_required_to_register_explicit_commands(self):
        self.configs.addon = None
        self.ui.register()
        self.assertTrue(all(cls.is_registered for cls in self.ui.CLASSES))

    def test_bind_registration_failure_preserves_preexisting_commands(self):
        self.operator.is_registered = True
        self.package.natural_bind_mode.register.side_effect = RuntimeError('bind mode error')
        with self.assertRaisesRegex(RuntimeError, 'bind mode error'):
            self.ui.register()
        self.assertTrue(self.operator.is_registered)
        self.assertFalse(any(cls.is_registered for cls in self.ui.CLASSES[1:]))

    def test_legacy_explicit_clear_channel_operator_delegates_only_requested_channel(self):
        ops = self.bpy.ops.pose
        ops.scale_clear = Mock(return_value={'FINISHED'})
        ops.loc_clear = Mock(return_value={'FINISHED'})
        command = self.ui.CHARACTERDESIGNER_OT_clear_body_transform_channel()
        for channel in ('LOCATION', 'SCALE', 'LOCATION', 'SCALE'):
            command.channel = channel
            clear, other = (ops.loc_clear, ops.scale_clear) if channel == 'LOCATION' else (ops.scale_clear, ops.loc_clear)
            clear.reset_mock()
            other.reset_mock()
            self.assertEqual(command.execute(self.context), {'FINISHED'})
            clear.assert_called_once_with('EXEC_DEFAULT', False)
            other.assert_not_called()
            ops.rot_clear.assert_not_called()

    def test_removed_chain_command_is_absent_from_registration_and_keymap(self):
        self.ui.register()
        removed = 'character_designer.reset_selected_pose_chains'
        self.assertNotIn(removed, [cls.bl_idname for cls in self.ui.CLASSES])
        self.assertEqual(self.addon_map.keymap_items.values, [self.ui._KEYMAPS[0][1], self.foreign])

    def test_second_class_registration_fault_rolls_back_only_owned_new_class(self):
        def register(cls):
            if cls is self.ui.CLASSES[1]:
                raise RuntimeError('new class fault')
            cls.is_registered = True
        self.bpy.utils.register_class.side_effect = register
        with self.assertRaisesRegex(RuntimeError, 'new class fault'):
            self.ui.register()
        self.assertFalse(self.ui.CLASSES[0].is_registered)
        self.assertFalse(self.ui.CLASSES[1].is_registered)
        self.assertEqual(self.addon_map.keymap_items.values, [self.foreign])


if __name__ == '__main__':
    unittest.main()
