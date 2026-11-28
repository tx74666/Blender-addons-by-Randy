"""Complementary controller-clear policy regressions without Blender.

Exercise the actual UI adapter and existing Foot Reset definition. Native
geometry, rotation conversion and physical shortcut precedence require native
validation; these tests prove dispatch/channel/selection ownership only.
"""
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import test_limb_ik_wrist_rotation_ui as wrist_fixture


SOURCE = Path(os.environ.get('CD_CONTROLLER_CLEAR_TEST_SOURCE',
    Path(__file__).resolve().parents[1] / 'addons/character_designer'))

ROTATION_FIELDS = ('rotation_euler', 'rotation_quaternion', 'rotation_axis_angle')
BEND_FIELDS = ('bbone_rollin', 'bbone_rollout', 'bbone_curveinx', 'bbone_curveinz',
               'bbone_curveoutx', 'bbone_curveoutz')


class PoseBones(dict):
    def __iter__(self):
        return iter(self.values())


class PoseContext(NS):
    @property
    def selected_pose_bones(self):
        return [pb for pb in self.object.pose.bones if pb.select]


def channels(pb):
    return {field: getattr(pb, field) for field in
            ('rotation_mode', 'location', 'scale', *ROTATION_FIELDS, *BEND_FIELDS)}


class Copyable:
    def copy(self):
        return Copyable()


class ControllerClearPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = wrist_fixture.WristRotationUI('runTest')
        with patch.object(wrist_fixture, 'SOURCE', SOURCE):
            self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def mixed_fixture(self, *, mode='XYZ', mirror=False):
        h = self.fixture
        h.load_backend()
        h.backend.prepare_wrist_neutral = Mock(return_value={'phase': 'prepared'})
        h.backend.apply_wrist_neutral = Mock()
        hand = h.hand
        foot = h.bone('Foot.L', 'FOOT_IK')
        other = h.bone('Foot.R', 'FOOT_IK')
        pole = h.bone('Pole.L', 'POLE')
        for index, pb in enumerate((hand, foot, other, pole)):
            pb.select = pb is not other
            pb.rotation_mode = mode
            pb.location, pb.scale = (.2, -.4, .7), (1.2, .9, 1.05)
            pb.rotation_euler = (.2 + index, -.3, .6)
            pb.rotation_quaternion = (.8, .1 + index, .2, .3)
            pb.rotation_axis_angle = (.5 + index, 0., 1., 0.)
            for j, field in enumerate(BEND_FIELDS):
                setattr(pb, field, .15 + index + j)
            pb.path_from_id = lambda field='', name=pb.name: 'pose.bones["' + name + '"].' + field
        h.rig.pose = NS(bones=PoseBones((pb.name, pb) for pb in (hand, foot, other, pole)),
                        use_mirror_x=mirror)
        h.rig.animation_data = NS(drivers=[])
        h.context = PoseContext(mode='POSE', object=h.rig, active_pose_bone=foot,
            objects_in_mode=[h.rig], scene=NS(tool_settings=NS(use_keyframe_insert_auto=False)),
            view_layer=NS(update=Mock(), objects=NS(active=h.rig)))
        h.bpy.utils.flip_name = lambda name: name[:-2] + ('.R' if name.endswith('.L') else '.L')
        events = []
        h.backend.prepare_wrist_neutral.side_effect = lambda context, target, follow_forearm: (
            events.append('prepare') or {'phase': 'prepared', 'target': target})
        h.backend.apply_wrist_neutral.side_effect = lambda *args: events.append('wrist')

        def native(*args):
            events.append(('native', tuple(pb.name for pb in h.context.selected_pose_bones)))
            affected = list(h.context.selected_pose_bones)
            if mirror:
                for pb in tuple(affected):
                    sibling = h.rig.pose.bones.get(h.bpy.utils.flip_name(pb.name))
                    if sibling is not None and sibling not in affected:
                        affected.append(sibling)
            for pb in affected:
                field = ('rotation_quaternion' if pb.rotation_mode == 'QUATERNION' else
                         'rotation_axis_angle' if pb.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
                setattr(pb, field, (1., 0., 0., 0.) if field == 'rotation_quaternion'
                        else (0., 0., 1., 0.) if field == 'rotation_axis_angle' else (0., 0., 0.))
                for bend in BEND_FIELDS:
                    setattr(pb, bend, 0.)
            return {'FINISHED'}

        h.bpy.ops.pose.rot_clear = Mock(side_effect=native)
        return h, hand, foot, other, pole, events, native

    def test_registration_leaves_native_location_scale_and_other_shortcuts_exact(self):
        h = self.fixture
        items = []
        for config in (h.configs.default, h.configs.user, h.configs.addon):
            for event, name in (('G', 'pose.loc_clear'), ('S', 'pose.scale_clear')):
                item = config.keymaps['Pose'].keymap_items.new(name, event, 'PRESS', alt=True)
                items.append((item, item.idname, item.type, item.value, item.active, item.flags.copy()))
        before = list(h.addon_map.keymap_items)
        h.ui.register()
        h.ui.register()
        self.assertEqual(list(h.addon_map.keymap_items), [h.ui._KEYMAPS[1][1], h.ui._KEYMAPS[0][1], *before])
        self.assertFalse(h.ui.CHARACTERDESIGNER_OT_clear_body_transform_channel.poll(h.context))
        self.assertEqual([(item, item.idname, item.type, item.value, item.active, item.flags.copy())
                          for item, *_ in items], items)
        h.ui.unregister()
        self.assertEqual([(item, item.idname, item.type, item.value, item.active, item.flags.copy())
                          for item, *_ in items], items)

    def test_ordinary_only_rotation_selection_keeps_native_routing(self):
        h = self.fixture
        for role in ('FOOT_IK', 'POLE', 'ARM_FK', 'MASTER', 'FOOT_ROLL', 'TOE_BEND'):
            with self.subTest(role=role):
                h.context.selected_pose_bones = [h.bone(role + '.L', role)]
                self.assertFalse(h.operator.poll(h.context))
        h.bpy.ops.pose.rot_clear.assert_not_called()
        h.backend.reset_wrist_neutral.assert_not_called()

    def test_mixed_selection_scopes_native_clear_and_restores_selection_and_active(self):
        for mode in ('XYZ', 'QUATERNION', 'AXIS_ANGLE'):
            with self.subTest(mode=mode):
                h, hand, foot, other, pole, events, _native = self.mixed_fixture(mode=mode)
                before = {pb.name: channels(pb) for pb in h.rig.pose.bones}
                selection = tuple(h.context.selected_pose_bones)
                self.assertTrue(h.operator.poll(h.context))
                self.assertEqual(h.operator().execute(h.context), {'FINISHED'})
                self.assertEqual(events, ['prepare', ('native', (foot.name, pole.name)), 'wrist'])
                h.bpy.ops.pose.rot_clear.assert_called_once_with('EXEC_DEFAULT', False)
                h.backend.prepare_wrist_neutral.assert_called_once_with(h.context, target=hand, follow_forearm=False)
                h.backend.reset_wrist_neutral.assert_not_called()
                self.assertEqual(tuple(h.context.selected_pose_bones), selection)
                self.assertIs(h.context.active_pose_bone, foot)
                self.assertIs(h.context.view_layer.objects.active, h.rig)
                self.assertEqual(channels(hand), before[hand.name])
                self.assertEqual(channels(other), before[other.name])
                for pb in (foot, pole):
                    for field in ('rotation_mode', 'location', 'scale'):
                        self.assertEqual(getattr(pb, field), before[pb.name][field])
                # Reset the fixture's recorded calls for the next mode.
                h.backend.reset_wrist_neutral.reset_mock()

    def test_mixed_multirig_and_rotation_or_bendy_driver_refuse_before_writes(self):
        for cause in ('multirig', 'rotation_driver', 'bendy_driver'):
            with self.subTest(cause=cause):
                h, _hand, foot, _other, _pole, _events, _native = self.mixed_fixture()
                if cause == 'multirig':
                    h.context.objects_in_mode.append(object())
                else:
                    field = 'rotation_euler' if cause == 'rotation_driver' else 'bbone_rollin'
                    h.rig.animation_data.drivers = [NS(data_path=foot.path_from_id(field))]
                before = {pb.name: channels(pb) for pb in h.rig.pose.bones}
                self.assertEqual(h.operator().execute(h.context), {'CANCELLED'})
                self.assertEqual({pb.name: channels(pb) for pb in h.rig.pose.bones}, before)
                h.bpy.ops.pose.rot_clear.assert_not_called()
                h.backend.prepare_wrist_neutral.assert_not_called()
                h.backend.apply_wrist_neutral.assert_not_called()

    def test_mixed_native_or_wrist_failure_restores_native_rotations_bendy_and_selection(self):
        for failure in ('native_exception', 'native_cancel', 'wrist'):
            with self.subTest(failure=failure):
                h, _hand, foot, other, _pole, _events, native = self.mixed_fixture(mirror=True)
                before = {pb.name: channels(pb) for pb in h.rig.pose.bones}
                selection = tuple(h.context.selected_pose_bones)
                if failure == 'wrist':
                    h.backend.apply_wrist_neutral.side_effect = ValueError('current wrist refuses')
                else:
                    def partial_native(*args):
                        native(*args)
                        if failure == 'native_exception':
                            raise RuntimeError('native clear failed after writing')
                        return {'CANCELLED'}
                    h.bpy.ops.pose.rot_clear.side_effect = partial_native
                self.assertEqual(h.operator().execute(h.context), {'CANCELLED'})
                self.assertEqual({pb.name: channels(pb) for pb in h.rig.pose.bones}, before)
                self.assertEqual(tuple(h.context.selected_pose_bones), selection)
                self.assertFalse(other.select)
                self.assertIs(h.context.active_pose_bone, foot)

    def test_mirrored_rotation_driver_is_guarded_but_unwritten_location_driver_is_allowed(self):
        for field, expected in (('rotation_quaternion', {'CANCELLED'}), ('location', {'FINISHED'})):
            with self.subTest(field=field):
                h, _hand, _foot, other, _pole, _events, _native = self.mixed_fixture(mirror=True)
                driver = NS(data_path=other.path_from_id(field))
                h.rig.animation_data.drivers = [driver]
                self.assertEqual(h.operator().execute(h.context), expected)
                self.assertIs(h.rig.animation_data.drivers[0], driver)
                self.assertEqual(h.bpy.ops.pose.rot_clear.call_count, int(expected == {'FINISHED'}))


class FootResetExistingPolicyTests(unittest.TestCase):
    def fixture(self):
        tree = ast.parse((SOURCE / 'limb_ik.py').read_bytes())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'CHARACTERDESIGNER_OT_limb_ik_reset_target_rotation')
        target = NS(name='Foot Target.L', bone={'role': 'FOOT_IK'},
            rotation_mode='QUATERNION', rotation_euler=(.3, -.2, .7),
            rotation_quaternion=(.7, .1, .2, .3), rotation_axis_angle=(.4, 1., 0., 0.),
            location=(.2, -.7, .9), scale=(1.2, .8, 1.1), matrix_basis=Copyable(),
            lock_rotation=(False, False, False), lock_rotation_w=False)
        rig = NS(update_tag=Mock())
        inventory = {'target_rotation_version': 2}
        context = NS(view_layer=NS(update=Mock()))
        scope = dict(Operator=object, ROLE_KEY='role', TARGET_ROTATION_VERSION=2,
            LimbIKError=type('LimbIKError', (ValueError,), {}), _settings=lambda context: NS(),
            _active_limb_target=lambda context, **kwargs: (rig, target, inventory, {}),
            _target_rotation_has_driver=Mock(return_value=False), _set_status=Mock())
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(SOURCE / 'limb_ik.py'), 'exec'), scope)
        operator = scope[cls.name]()
        operator.report = Mock()
        return scope, operator, context, target, rig, inventory

    def test_explicit_foot_reset_remains_local_xyz_zero_with_location_and_scale_kept(self):
        scope, operator, context, target, rig, _inventory = self.fixture()
        before = (target.location, target.scale, target.rotation_quaternion, target.rotation_axis_angle)
        self.assertEqual(operator.execute(context), {'FINISHED'})
        self.assertEqual(target.rotation_mode, 'XYZ')
        self.assertEqual(target.rotation_euler, (0., 0., 0.))
        self.assertEqual((target.location, target.scale, target.rotation_quaternion, target.rotation_axis_angle), before)
        scope['_target_rotation_has_driver'].assert_called_once_with(rig, target.name)
        context.view_layer.update.assert_called_once_with()

    def test_foot_version_lock_and_driver_refusals_keep_all_channels(self):
        for reason in ('version', 'xyz_lock', 'w_lock', 'driver'):
            with self.subTest(reason=reason):
                scope, operator, context, target, _rig, inventory = self.fixture()
                if reason == 'version':
                    inventory['target_rotation_version'] = -1
                elif reason == 'xyz_lock':
                    target.lock_rotation = (False, True, False)
                elif reason == 'w_lock':
                    target.lock_rotation_w = True
                else:
                    scope['_target_rotation_has_driver'].return_value = True
                before = vars(target).copy()
                self.assertEqual(operator.execute(context), {'CANCELLED'})
                self.assertEqual(vars(target), before)
                context.view_layer.update.assert_not_called()


if __name__ == '__main__':
    unittest.main()
