"""Pure pair admission/atomic transaction contracts; native math is separate."""
from copy import deepcopy
import importlib
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import test_limb_ik_wrist_rotation as single

service = single.wrist
batch = importlib.import_module(single.PACKAGE + '.limb_ik_wrist_batch')


def fixture(*, mirror=True, action=True, autokey=True):
    f = single.fixture(action=action, autokey=autokey)
    right = [single.Bone(n) for n in ('target.R', 'hand.R', 'forearm.R', 'pole.R')]
    f.right, f.right_hand, parent, pole = right
    f.right_hand.parent = parent
    f.hand.children_recursive = f.right_hand.children_recursive = []
    end = NS(mute=True, influence=1., target_space='WORLD', owner_space='WORLD')
    offset = NS(mute=False, influence=1.)
    f.right_hand.constraints = [end, offset]
    f.inventory['rigs'][('ARM', 'R')] = dict(target=f.right.bone, solver_target=f.right.bone,
        pole=pole.bone, chain=('upper.R', parent.name, f.right_hand.name), auto_align=True,
        auto_rotation_space='PARENT_DELTA', auto_offset_rotation=offset,
        entries=[(f.right_hand, end, {'role': 'END_ROTATION'}),
                 (f.right_hand, offset, {'role': 'AUTO_OFFSET_ROTATION'})])
    for pb in right: f.rig.pose.bones[pb.name] = pb
    f.rig.pose.use_mirror_x = mirror
    f.variant.update(source=object(), source_slot=7, source_hash='source-original',
        hashes={7: 'original'}, row_identity={'item_id': 'a'*32})
    f.keys = {f.target.name: 'old-left', f.right.name: 'old-right'}
    f.key_before = dict(f.keys)
    f.events = []
    return f


def prepared(f, **arguments):
    def reference(api, rig, end):
        return {f.hand.name: 'left-zero' if end is f.hand else 'left-before',
                f.right_hand.name: 'right-zero' if end is f.right_hand else 'right-before'}
    with single.backend(f), patch.object(service, '_reference', side_effect=reference), \
         patch.object(service, '_matrix_finite', return_value=True), \
         patch.object(service, '_keying', return_value=f.keying):
        return service.prepare_wrist_neutral(f.context, **arguments)


def execute(f, plan, *, fault=None, key_failure=None):
    def restore(context, rig, capture, api):
        for name, raw in plan['before_raw'].items():
            pb = rig.pose.bones[name]
            for prop in service._TRANSFORMS: setattr(pb, prop, raw[prop])
    f.api.revision._restore_current.side_effect = restore
    def solve(context, side):
        setattr(side['target'], side['rotation_path'], (.2, -.3, .4))
        service._fault('after_rotation_write')
        return 1
    def verify(context, side):
        service._unchanged_inputs(side, allow_target_rotation=True)
        return {'matrix': 0.}
    def key(context, side):
        name = side['target'].name
        def restore_keys():
            f.events.append('restore:' + name)
            f.keys[name] = f.key_before[name]
        def verify_keys():
            f.events.append('verify:' + name)
            if f.keys != f.key_before: raise ValueError('Global original Action hash differs')
        if not side['autokey']: return False
        side['key_restore'], side['key_verify'] = restore_keys, verify_keys
        side['key_commit'] = lambda: f.events.append('commit:' + name)
        f.keys[name] = 'new-' + name
        if name == key_failure: raise RuntimeError('actual partial key failure')
        return True
    with single.backend(f), patch.object(service, '_keying', return_value=f.keying), \
         patch.object(service, '_solve', side_effect=solve), patch.object(service, '_verify', side_effect=verify), \
         patch.object(service, '_key_rotation', side_effect=key), patch.object(service, '_fault', side_effect=fault):
        return service.apply_wrist_neutral(f.context, plan)


class PairAdmission(unittest.TestCase):
    def test_mirror_single_authenticates_both_and_keeps_independent_references(self):
        f = fixture(); before = service._raw(f.rig)
        plan = prepared(f, target=f.right)
        self.assertEqual(tuple(p['key'] for p in plan['sides']), (('ARM','L'), ('ARM','R')))
        self.assertEqual(plan['wanted_native'], {'hand.L': 'left-zero', 'hand.R': 'right-zero'})
        self.assertEqual(service._raw(f.rig), before)
        self.assertTrue(f.rig.pose.use_mirror_x)

    def test_explicit_pair_without_mirror_is_supported_and_duplicates_refuse(self):
        f = fixture(mirror=False)
        self.assertIn('batch_api', prepared(f, targets=(f.right, f.target)))
        with self.assertRaisesRegex(ValueError, 'left and right'):
            prepared(f, targets=(f.target, f.target))

    def test_edit_mirror_does_not_expand_or_refuse_a_pose_single(self):
        f = fixture(mirror=False); f.rig.data.use_mirror_x = True
        plan = prepared(f, target=f.target)
        self.assertNotIn('batch_api', plan)
        self.assertEqual(plan['mirror_flags'], (False, True))

    def test_opposite_refusal_is_before_either_rotation_write(self):
        for reason in ('missing', 'FK', 'lock', 'driver'):
            with self.subTest(reason=reason):
                f = fixture()
                if reason == 'missing': del f.inventory['rigs'][('ARM','R')]
                if reason == 'FK': f.right['ik_fk'] = 0.
                if reason == 'lock': f.right.lock_rotation = (True, False, False)
                if reason == 'driver':
                    f.rig.animation_data.drivers = [NS(data_path=f.right.path_from_id('rotation_euler'))]
                before = service._raw(f.rig)
                with self.assertRaises(ValueError): prepared(f, target=f.target)
                self.assertEqual(service._raw(f.rig), before)

    def test_side_plan_cannot_be_applied_as_a_standalone_operation(self):
        f = fixture(); plan = prepared(f, target=f.target)
        with self.assertRaisesRegex(ValueError, 'complete prepared pair'):
            service.apply_wrist_neutral(f.context, plan['sides'][0])


class PairTransactions(unittest.TestCase):
    def test_success_writes_both_and_commits_after_all_verification(self):
        f = fixture(); plan = prepared(f, target=f.target)
        result = execute(f, plan)
        self.assertEqual(result['targets'], ('target.L', 'target.R'))
        self.assertTrue(result['keyed']); self.assertEqual(plan['phase'], 'applied')
        self.assertEqual(f.events, ['commit:target.L','commit:target.R'])
        self.assertEqual((f.rig.pose.use_mirror_x, f.rig.data.use_mirror_x), (True,False))
        f.offset._variant_owner.assert_any_call(f.variant, final=False)

    def test_all_five_faults_restore_both_raw_inputs_and_all_keys(self):
        phases = ('after_batch_first_rotation','after_batch_second_rotation','after_batch_target_verification',
                  'after_batch_first_key','after_batch_second_key')
        for phase in phases:
            with self.subTest(phase=phase):
                f = fixture(); plan = prepared(f, target=f.target)
                def fault(actual):
                    if actual == phase: raise RuntimeError('injected '+phase)
                with self.assertRaisesRegex(RuntimeError,'injected'):
                    execute(f, plan, fault=fault)
                self.assertEqual(service._raw(f.rig), plan['before_raw'])
                self.assertEqual(f.keys, f.key_before); self.assertEqual(plan['phase'],'rolled_back')
                self.assertFalse(any(e.startswith('commit:') for e in f.events))
                restores = [i for i,e in enumerate(f.events) if e.startswith('restore:')]
                verifies = [i for i,e in enumerate(f.events) if e.startswith('verify:')]
                if restores and verifies: self.assertLess(max(restores), min(verifies))

    def test_second_key_can_fail_after_write_and_still_restore_both_domains(self):
        f = fixture(); plan = prepared(f, target=f.target)
        with self.assertRaisesRegex(RuntimeError,'actual partial key failure'):
            execute(f, plan, key_failure=f.right.name)
        self.assertEqual(f.keys, f.key_before)
        self.assertEqual(service._raw(f.rig), plan['before_raw'])
        self.assertEqual(f.events, ['restore:target.R','restore:target.L','verify:target.L','verify:target.R'])

    def test_changed_mirror_flag_or_native_input_refuses_before_solver(self):
        for reason in ('mirror', 'native'):
            with self.subTest(reason=reason):
                f = fixture(); plan = prepared(f, target=f.target)
                if reason == 'mirror': f.rig.pose.use_mirror_x = False
                else: f.hand.rotation_euler = (.1,.2,.3)
                with single.backend(f), patch.object(service,'_keying',return_value=f.keying), \
                     patch.object(service,'_solve') as solve:
                    with self.assertRaises(ValueError): service.apply_wrist_neutral(f.context,plan)
                solve.assert_not_called(); self.assertEqual(plan['phase'],'prepared')


if __name__ == '__main__': unittest.main()
