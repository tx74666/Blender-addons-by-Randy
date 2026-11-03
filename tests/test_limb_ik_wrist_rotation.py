"""Pure admission/transaction contracts; native wrist math has separate tests."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'
PACKAGE = '_wrist_neutral_pure_test'
package = ModuleType(PACKAGE); package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + '.limb_ik_wrist_rotation', ROOT / 'limb_ik_wrist_rotation.py')
wrist = importlib.util.module_from_spec(spec); sys.modules[spec.name] = wrist
spec.loader.exec_module(wrist)


class Bone(dict):
    def __init__(self, name):
        super().__init__(ik_fk=1.)
        self.name, self.bone = name, NS(name=name)
        self.rotation_mode = 'XYZ'
        self.location, self.scale = (0., 0., 0.), (1., 1., 1.)
        self.rotation_euler = (0., 0., 0.)
        self.rotation_quaternion = (1., 0., 0., 0.)
        self.rotation_axis_angle = (0., 0., 1., 0.)
        self.constraints, self.lock_rotation, self.lock_rotation_w = [], (False, False, False), False
        self.matrix = Matrix()
    def path_from_id(self, prop=None):
        return 'pose.bones["' + self.name + '"]' + ('.' + prop if prop else '')


class Bones(dict):
    def __iter__(self): return iter(self.values())


class Vector(tuple):
    def copy(self): return Vector(self)


class Matrix:
    def __init__(self): self.translation = Vector((0., 0., 0.))
    def copy(self): return deepcopy(self)


class Rig(dict):
    pass


def fixture(*, action=False, autokey=False):
    target, hand, forearm, pole = [Bone(name) for name in ('target.L', 'hand.L', 'forearm.L', 'pole.L')]
    hand.parent = forearm
    end_rotation = NS(mute=True, influence=1., target_space='WORLD', owner_space='WORLD')
    offset_rotation = NS(mute=False, influence=1.)
    hand.constraints = [end_rotation, offset_rotation]
    entry = dict(target=target.bone, solver_target=target.bone, pole=pole.bone,
        chain=('upper.L', forearm.name, hand.name), auto_align=True, auto_rotation_space='PARENT_DELTA',
        auto_offset_rotation=offset_rotation, entries=[(hand, end_rotation, {'role': 'END_ROTATION'}),
                                                     (hand, offset_rotation, {'role': 'AUTO_OFFSET_ROTATION'})])
    inventory = {'rigs': {('ARM', 'L'): entry}}
    rig = Rig(); rig.type = 'ARMATURE'; rig.data = NS(use_mirror_x=False)
    rig.pose = NS(bones=Bones({b.name: b for b in (target, hand, forearm, pole)}), use_mirror_x=False)
    rig.matrix_world = Matrix(); rig.rotation_mode = 'XYZ'
    for prop in wrist._TRANSFORMS: setattr(rig, prop, getattr(target, prop))
    active_action = object() if action else None
    rig.animation_data = NS(action=active_action, action_slot=NS(handle=7), drivers=[],
                            use_nla=False, nla_tracks=[])
    context = NS(object=rig, active_pose_bone=target, mode='POSE', screen=NS(is_animation_playing=False),
        selected_objects=[rig], view_layer=NS(objects=NS(active=rig)),
        scene=NS(frame_current=14, frame_subframe=0., tool_settings=NS(use_keyframe_insert_auto=autokey),
                 keying_sets=NS(active=None), keying_sets_all=NS(active=None)))
    variant = dict(slot=7, revision_value=None, item_id='a'*32)
    offset = NS(prepare_variant=Mock(return_value=variant), _variant_owner=Mock(),
                _unaffected=Mock(return_value='exact-unaffected'))
    api = NS(pose=NS(_admission=Mock(), active=Mock(return_value=False), natural_active=Mock(return_value=False),
                     _structure=Mock(return_value='rig-structure')),
        limb=NS(_validate_inventory=Mock(return_value=inventory)),
        match=NS(PROPERTY='ik_fk', mode_for_rig=Mock(return_value='IK'),
                 property_path=lambda pb: pb.path_from_id()+'["ik_fk"]'),
        target=NS(_capture=Mock(return_value={'fresh': 'capture'}), _validate_capture=Mock()),
        revision=NS(_modules=Mock(return_value='revision-api'), _restore_current=Mock(), validate=Mock()))
    physics = NS(status=Mock(return_value={'rig': rig, 'valid': True, 'hair': {}, 'dresses': []}))
    keying = NS(prepare_keying=Mock(), validate_keying=Mock(), key_rotation=Mock(return_value=autokey))
    return NS(rig=rig, target=target, hand=hand, forearm=forearm, pole=pole, context=context,
        entry=entry, inventory=inventory, end_rotation=end_rotation, offset_rotation=offset_rotation,
        offset=offset, api=api, physics=physics, keying=keying, variant=variant)


def backend(f):
    return patch.object(wrist, '_backend', return_value=(f.offset, f.api, f.physics))


def prepared(f):
    with backend(f), patch.object(wrist, '_reference', return_value={'native': 'reference'}), \
         patch.object(wrist, '_matrix_finite', return_value=True), \
         patch.object(wrist, '_keying', return_value=f.keying):
        return wrist.prepare_wrist_neutral(f.context)


class AdmissionTests(unittest.TestCase):
    def test_no_action_pose_only_is_admitted_and_keying_prepared_without_pose_writes(self):
        f = fixture(); before = wrist._raw(f.rig)
        plan = prepared(f)
        self.assertIsNone(plan['action']); self.assertIsNone(plan['variant'])
        f.keying.prepare_keying.assert_called_once_with(f.context, plan)
        f.offset.prepare_variant.assert_not_called()
        self.assertEqual(wrist._raw(f.rig), before)

    def test_action_authentication_is_required_even_with_autokey_off(self):
        f = fixture(action=True)
        f.offset.prepare_variant.side_effect = ValueError('Source is read-only')
        before = wrist._raw(f.rig)
        with backend(f):
            with self.assertRaisesRegex(ValueError, 'Source is read-only'):
                wrist.prepare_wrist_neutral(f.context)
        self.assertEqual(wrist._raw(f.rig), before)

    def test_no_action_autokey_refuses_before_capture_or_keying(self):
        f = fixture(autokey=True)
        with backend(f), patch.object(wrist, '_keying', return_value=f.keying):
            with self.assertRaisesRegex(ValueError, 'editable Custom'):
                wrist.prepare_wrist_neutral(f.context)
        f.api.target._capture.assert_not_called(); f.keying.prepare_keying.assert_not_called()

    def test_key_service_refusal_happens_before_any_rotation_write(self):
        f = fixture(action=True, autokey=True); before = wrist._raw(f.rig)
        f.keying.prepare_keying.side_effect = ValueError('Locked Action curve')
        with self.assertRaisesRegex(ValueError, 'Locked Action curve'):
            prepared(f)
        self.assertEqual(wrist._raw(f.rig), before)

    def test_actual_ik_and_owned_graph_are_required(self):
        cases = {
            'FK raw value': lambda f: f.target.update(ik_fk=0.),
            'blend mode': lambda f: setattr(f.api.match.mode_for_rig, 'return_value', 'BLEND'),
            'foreign target': lambda f: f.entry.update(target=NS(name=f.target.name)),
            'foreign solver': lambda f: f.entry.update(solver_target=NS(name='other')),
            'parent changed': lambda f: setattr(f.hand, 'parent', Bone('other')),
            'rotation locked': lambda f: setattr(f.target, 'lock_rotation', (True, False, False)),
            'target constraint': lambda f: f.target.constraints.append(object()),
            'legacy auto': lambda f: f.entry.update(auto_rotation_space='LOCAL'),
            'wrong auto mute': lambda f: setattr(f.offset_rotation, 'mute', True),
            'unknown hand constraint': lambda f: f.hand.constraints.append(object()),
            'driven target rotation': lambda f: f.rig.animation_data.drivers.append(NS(data_path=f.target.path_from_id('rotation_euler'))),
            'driven mode': lambda f: f.rig.animation_data.drivers.append(NS(data_path=f.api.match.property_path(f.target))),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                f = fixture(); mutate(f); before = wrist._raw(f.rig)
                with backend(f):
                    with self.assertRaises(ValueError): wrist._admission(f.context)
                self.assertEqual(wrist._raw(f.rig), before)

    def test_manual_world_is_supported_but_other_spaces_refuse(self):
        f = fixture(); f.entry['auto_align'] = False
        f.end_rotation.mute, f.offset_rotation.mute = False, True
        with backend(f): wrist._admission(f.context)
        f.end_rotation.target_space = 'LOCAL'
        with backend(f):
            with self.assertRaisesRegex(ValueError, 'WORLD Manual'): wrist._admission(f.context)

    def test_pose_session_playback_mirror_keyingset_and_physics_refuse(self):
        cases = {
            'Original admission': lambda f: setattr(f.api.pose._admission, 'side_effect', ValueError('Original')),
            'Base Pose': lambda f: setattr(f.api.pose.active, 'return_value', True),
            'Natural': lambda f: setattr(f.api.pose.natural_active, 'return_value', True),
            'playback': lambda f: setattr(f.context.screen, 'is_animation_playing', True),
            'Pose mirror': lambda f: setattr(f.rig.pose, 'use_mirror_x', True),
            'data mirror': lambda f: setattr(f.rig.data, 'use_mirror_x', True),
            'Keying Set': lambda f: setattr(f.context.scene.keying_sets, 'active', object()),
            'Hair running': lambda f: f.physics.status.return_value.update(hair={'running': True}),
            'Dress recovery': lambda f: f.physics.status.return_value.update(dresses=[{'pending_recovery': True}]),
            'NLA strip': lambda f: (setattr(f.rig.animation_data, 'use_nla', True),
                f.rig.animation_data.nla_tracks.append(NS(mute=False, strips=[object()]))),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                f = fixture(); mutate(f); before = wrist._raw(f.rig)
                with backend(f):
                    with self.assertRaises(ValueError): wrist._admission(f.context)
                self.assertEqual(wrist._raw(f.rig), before)

    def test_existing_rotation_representation_is_preserved_in_plan(self):
        for mode, path in [('XYZ', 'rotation_euler'), ('ZYX', 'rotation_euler'),
                           ('QUATERNION', 'rotation_quaternion'), ('AXIS_ANGLE', 'rotation_axis_angle')]:
            with self.subTest(mode=mode):
                f = fixture(); f.target.rotation_mode = mode
                plan = prepared(f)
                self.assertEqual((plan['rotation_mode'], plan['rotation_path']), (mode, path))
                self.assertEqual(f.target.rotation_mode, mode)


class TransactionTests(unittest.TestCase):
    def execute(self, f, plan, *, fault=None, verify=None, key=None):
        def restore(context, rig, capture, api):
            for name, raw in plan['before_raw'].items():
                pb = rig.pose.bones[name]; pb.rotation_mode = raw['mode']
                for prop in wrist._TRANSFORMS: setattr(pb, prop, raw[prop])
        f.api.revision._restore_current.side_effect = restore
        def solve(context, plan):
            setattr(f.target, plan['rotation_path'], (.2, -.3, .4))
            wrist._fault('after_rotation_write')
            return 1
        with patch.object(wrist, '_validate_plan'), patch.object(wrist, '_solve', side_effect=solve), \
             patch.object(wrist, '_verify', verify or Mock(return_value={'matrix': 0.})), \
             patch.object(wrist, '_key_rotation', key or Mock(return_value=plan['autokey'])), \
             patch.object(wrist, '_fault', side_effect=fault):
            return wrist.apply_wrist_neutral(f.context, plan)

    def test_pose_only_success_changes_only_target_rotation_and_returns_real_key_bool(self):
        f = fixture(); plan = prepared(f)
        result = self.execute(f, plan)
        self.assertIs(result['keyed'], False); self.assertEqual(plan['phase'], 'applied')
        wrist._unchanged_inputs(plan, allow_target_rotation=True)
        self.assertNotEqual(f.target.rotation_euler, plan['before_raw'][f.target.name]['rotation_euler'])
        f.api.revision._restore_current.assert_not_called()

    def test_all_three_fault_seams_restore_fresh_pose_and_keys(self):
        for phase in ('after_rotation_write', 'after_target_verification', 'after_key_rotation'):
            with self.subTest(phase=phase):
                f = fixture(autokey=False); plan = prepared(f); calls = []
                plan['key_rollback'] = lambda: calls.append('rollback')
                def fault(actual):
                    if actual == phase: raise RuntimeError('injected ' + phase)
                with self.assertRaisesRegex(RuntimeError, 'injected'):
                    self.execute(f, plan, fault=fault)
                self.assertEqual(wrist._raw(f.rig), plan['before_raw'])
                self.assertEqual((plan['phase'], calls), ('rolled_back', ['rollback']))

    def test_key_write_then_exception_still_runs_preinstalled_key_rollback(self):
        f = fixture(action=True, autokey=True); plan = prepared(f); keys = ['old FK key']
        def key(context, plan):
            plan['key_rollback'] = lambda: keys.__setitem__(slice(None), ['old FK key'])
            keys.append('partial wrist key')
            raise RuntimeError('partial insert')
        with self.assertRaisesRegex(RuntimeError, 'partial insert'):
            self.execute(f, plan, key=key)
        self.assertEqual(keys, ['old FK key']); self.assertEqual(wrist._raw(f.rig), plan['before_raw'])

    def test_quality_refusal_precedes_keying(self):
        f = fixture(); plan = prepared(f); key = Mock()
        with self.assertRaisesRegex(ValueError, 'quality'):
            self.execute(f, plan, verify=Mock(side_effect=ValueError('quality refused')), key=key)
        key.assert_not_called(); self.assertEqual(plan['phase'], 'rolled_back')

    def test_wrong_key_policy_rolls_back_instead_of_claiming_success(self):
        f = fixture(action=True, autokey=True); plan = prepared(f)
        with self.assertRaisesRegex(ValueError, 'Auto Key policy'):
            self.execute(f, plan, key=Mock(return_value=False))
        self.assertEqual(plan['phase'], 'rolled_back')

    def test_successful_keying_never_compares_old_custom_fullhash_after_keys(self):
        f = fixture(action=True, autokey=True); plan = prepared(f); commit = Mock()
        plan['key_commit'] = commit
        result = self.execute(f, plan)
        self.assertIs(result['keyed'], True)
        f.offset._variant_owner.assert_called_once_with(plan['variant'], final=False)
        commit.assert_called_once()

    def test_unrelated_curve_change_rolls_back_and_does_not_commit(self):
        f = fixture(action=True, autokey=True); plan = prepared(f); commit = Mock()
        f.offset._unaffected.return_value = 'foreign FK curve change'; plan['key_commit'] = commit
        with self.assertRaisesRegex(ValueError, 'unrelated native FK'):
            self.execute(f, plan)
        commit.assert_not_called(); self.assertEqual(plan['phase'], 'rolled_back')

    def test_key_recovery_failure_still_attempts_pose_recovery(self):
        f = fixture(); plan = prepared(f)
        plan['key_rollback'] = Mock(side_effect=RuntimeError('curve restore failed'))
        with self.assertRaisesRegex(RuntimeError, 'Owned wrist recovery needs review'):
            self.execute(f, plan, verify=Mock(side_effect=ValueError('quality refused')))
        f.api.revision._restore_current.assert_called_once()
        self.assertEqual(plan['phase'], 'recovery_needed')


class WitnessTests(unittest.TestCase):
    def test_changed_keying_policy_refuses_before_solver_or_any_rotation_write(self):
        f = fixture(action=True, autokey=True); plan = prepared(f)
        before = wrist._raw(f.rig)
        f.keying.validate_keying.side_effect = ValueError('keying policy changed')
        with backend(f), patch.object(wrist, '_keying', return_value=f.keying), \
             patch.object(wrist, '_solve') as solve:
            with self.assertRaisesRegex(ValueError, 'keying policy changed'):
                wrist.apply_wrist_neutral(f.context, plan)
        f.keying.validate_keying.assert_called_once_with(f.context, plan)
        solve.assert_not_called(); f.api.revision._restore_current.assert_not_called()
        self.assertEqual(wrist._raw(f.rig), before); self.assertEqual(plan['phase'], 'prepared')

    def test_stale_frame_or_changed_native_input_refuses_before_solver(self):
        for field in ('frame', 'native'):
            with self.subTest(field=field):
                f = fixture(); plan = prepared(f)
                if field == 'frame': f.context.scene.frame_subframe = .5
                else: f.hand.rotation_euler = (.1, .2, .3)
                with backend(f), patch.object(wrist, '_solve') as solve:
                    with self.assertRaises(ValueError): wrist.apply_wrist_neutral(f.context, plan)
                solve.assert_not_called(); self.assertEqual(plan['phase'], 'prepared')

    def test_target_rotation_allowance_does_not_allow_location_scale_or_native_edits(self):
        for name, prop, value in [('target', 'location', (1., 0., 0.)),
                                  ('target', 'scale', (2., 1., 1.)),
                                  ('hand', 'rotation_euler', (.1, 0., 0.))]:
            with self.subTest(name=name, prop=prop):
                f = fixture(); plan = prepared(f); setattr(getattr(f, name), prop, value)
                with self.assertRaisesRegex(ValueError, 'Another native or control input'):
                    wrist._unchanged_inputs(plan, allow_target_rotation=True)

    def test_completed_or_foreign_context_plan_is_not_replayed(self):
        f = fixture(); plan = prepared(f); plan['phase'] = 'applied'
        with self.assertRaises(ValueError): wrist._validate_plan(f.context, plan)
        plan['phase'] = 'prepared'
        with self.assertRaises(ValueError): wrist._validate_plan(NS(), plan)


if __name__ == '__main__':
    unittest.main()
