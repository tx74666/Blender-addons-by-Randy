"""Operation-level wrist contracts using the preserved 139/140 pure fixture.

These tests exercise real admission/prepare/apply/rollback routing. They replace
native solving and complete content hashing with observable test doubles; native
matrix/keyframe correctness and the digest implementation are separate checks.
"""
from contextlib import contextmanager
from copy import deepcopy
import importlib
import unittest
from unittest.mock import Mock, patch

import test_limb_ik_wrist_rotation as single
import test_limb_ik_wrist_batch as pair

service = single.wrist
transaction = importlib.import_module(single.PACKAGE + '.limb_ik_wrist_transaction')


def fixture(*, mirror=False, action=True, autokey=False):
    f = pair.fixture(mirror=mirror, action=action, autokey=autokey)
    f.contents = dict(source='original-source', foreign='original-unrelated', owner='original-row')
    f.reads = []

    def custom():
        return tuple(sorted(f.keys.items())), f.contents['foreign']

    def prepare(context, rig):
        assert context is f.context and rig is f.rig
        f.reads.append(('prepare', custom(), f.contents['source']))
        capture = f.api.target._capture(context, rig)
        f.variant.update(phase='prepared', capture=capture, original=rig.animation_data.action,
            baseline_custom=custom(), baseline_source=f.contents['source'], baseline_owner=f.contents['owner'])
        return f.variant

    def validate(variant, *, final=False):
        assert variant is f.variant
        f.reads.append(('validate', final, custom(), f.contents['source']))
        if f.contents['owner'] != variant['baseline_owner']:
            raise ValueError('fresh complete owner changed')
        if f.contents['source'] != variant['baseline_source']:
            raise ValueError('fresh complete Source changed')
        if final and custom() != variant['baseline_custom']:
            raise ValueError('fresh complete Custom changed')

    def unaffected(api, action, slot, affected):
        assert api is f.api and action is f.rig.animation_data.action and slot == 7
        f.reads.append(('unaffected', frozenset(affected), f.contents['foreign']))
        return f.contents['foreign']

    f.proof = single.NS(prepare_variant=Mock(side_effect=prepare),
        validate_variant=Mock(side_effect=validate), unaffected=Mock(side_effect=unaffected))
    return f


@contextmanager
def backend(f):
    with single.backend(f), patch.object(transaction, '_proof', return_value=f.proof), \
         patch.object(service, '_keying', return_value=f.keying):
        yield


def prepare(f, **arguments):
    def reference(api, rig, end):
        return {f.hand.name: 'left-zero' if end is f.hand else 'left-before',
                f.right_hand.name: 'right-zero' if end is f.right_hand else 'right-before'}
    with backend(f), patch.object(service, '_reference', side_effect=reference), \
         patch.object(service, '_matrix_finite', return_value=True):
        return service.prepare_wrist_neutral(f.context, **arguments)


def execute(f, plan, *, fault=None, key_failure=None, after_key=None):
    def restore(context, rig, capture, api):
        for name, raw in plan['before_raw'].items():
            pb = rig.pose.bones[name]
            pb.rotation_mode = raw['mode']
            for prop in service._TRANSFORMS:
                setattr(pb, prop, raw[prop])
    f.api.revision._restore_current.side_effect = restore

    def solve(context, side):
        setattr(side['target'], side['rotation_path'], (.2, -.3, .4))
        service._fault('after_rotation_write')
        return 1

    def verify(context, side):
        service._unchanged_inputs(side, allow_target_rotation=True)
        return {'matrix': 0.}

    def key(context, side):
        if not side['autokey']:
            return False
        name = side['target'].name
        def restore_keys():
            f.events.append('restore:' + name)
            f.keys[name] = f.key_before[name]
        def verify_keys():
            f.events.append('verify:' + name)
            if f.keys != f.key_before:
                raise ValueError('Complete original key domains differ')
        side['key_restore'], side['key_verify'] = restore_keys, verify_keys
        side['key_rollback'] = lambda: (restore_keys(), verify_keys())
        side['key_commit'] = lambda: f.events.append('commit:' + name)
        f.keys[name] = 'new-' + name
        if after_key is not None:
            after_key(side)
        if name == key_failure:
            raise RuntimeError('partial native key insertion')
        return True

    with backend(f), patch.object(service, '_solve', side_effect=solve), \
         patch.object(service, '_verify', side_effect=verify), \
         patch.object(service, '_key_rotation', side_effect=key), \
         patch.object(service, '_fault', side_effect=fault):
        return service.apply_wrist_neutral(f.context, plan)


class SharedPreparation(unittest.TestCase):
    def test_single_and_pair_share_one_full_preparation_and_capture(self):
        for mirrored in (False, True):
            with self.subTest(mirrored=mirrored):
                f = fixture(mirror=mirrored, autokey=True)
                plan = prepare(f)
                shared = plan['transaction']
                sides = plan.get('sides', (plan,))
                f.proof.prepare_variant.assert_called_once_with(f.context, f.rig)
                f.api.target._capture.assert_called_once_with(f.context, f.rig)
                self.assertTrue(all(p['transaction'] is shared and p['capture'] is shared['capture']
                                    and p['variant'] is f.variant for p in sides))
                self.assertEqual(len(shared['affected']), 6 if mirrored else 3)
                f.proof.unaffected.assert_called_once_with(f.api, shared['action'], 7, shared['affected'])
                f.offset.prepare_variant.assert_not_called()
                f.offset._unaffected.assert_not_called()

    def test_autokey_off_uses_complete_custom_instead_of_redundant_unaffected(self):
        f = fixture(mirror=True)
        plan = prepare(f)
        execute(f, plan)
        self.assertEqual([c.kwargs['final'] for c in f.proof.validate_variant.call_args_list], [True, True])
        f.proof.unaffected.assert_not_called()

    def test_no_action_pose_only_captures_once_without_any_action_proof(self):
        f = fixture(mirror=True, action=False)
        plan = prepare(f)
        result = execute(f, plan)
        self.assertFalse(result['keyed'])
        f.api.target._capture.assert_called_once_with(f.context, f.rig)
        f.proof.prepare_variant.assert_not_called()
        f.proof.validate_variant.assert_not_called()
        self.assertEqual(plan['transaction']['phase'], 'complete')

    def test_no_action_autokey_refuses_before_capture_or_keys(self):
        f = fixture(action=False, autokey=True)
        with self.assertRaisesRegex(ValueError, 'editable Custom'):
            prepare(f)
        f.api.target._capture.assert_not_called()
        f.keying.prepare_keying.assert_not_called()


class FreshProofs(unittest.TestCase):
    def test_source_custom_and_owner_changes_between_prepare_apply_refuse_before_write(self):
        for mirrored in (False, True):
            for field in ('source', 'foreign', 'owner'):
                with self.subTest(mirrored=mirrored, field=field):
                    f = fixture(mirror=mirrored, autokey=True)
                    plan = prepare(f)
                    before = service._raw(f.rig)
                    f.contents[field] = 'changed-after-preparation'
                    with backend(f), patch.object(service, '_solve') as solve:
                        with self.assertRaisesRegex(ValueError, 'fresh complete'):
                            service.apply_wrist_neutral(f.context, plan)
                    solve.assert_not_called()
                    f.api.revision._restore_current.assert_not_called()
                    self.assertEqual(service._raw(f.rig), before)
                    self.assertEqual(f.keys, f.key_before)

    def test_apply_revalidates_once_and_keyed_finish_uses_union_unaffected(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        execute(f, plan)
        self.assertEqual(f.proof.prepare_variant.call_count, 1)
        self.assertEqual([c.kwargs['final'] for c in f.proof.validate_variant.call_args_list], [True, False])
        self.assertEqual(f.proof.unaffected.call_count, 2)
        self.assertTrue(all(c.args[3] == plan['transaction']['affected']
                            for c in f.proof.unaffected.call_args_list))
        self.assertEqual(f.events, ['commit:target.L', 'commit:target.R'])
        f.offset.prepare_variant.assert_not_called()
        f.offset._variant_owner.assert_not_called()
        f.offset._unaffected.assert_not_called()
        f.api.revision.validate.assert_not_called()

    def test_final_unrelated_mutation_does_not_commit_or_overwrite_foreign_data(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        def foreign_after_key(side):
            if side['target'] is f.right:
                f.contents['foreign'] = 'unexpected-real-foreign-change'
        with self.assertRaisesRegex(RuntimeError, 'recovery needs review'):
            execute(f, plan, after_key=foreign_after_key)
        self.assertEqual(f.keys, f.key_before)
        self.assertEqual(service._raw(f.rig), plan['before_raw'])
        self.assertEqual(f.contents['foreign'], 'unexpected-real-foreign-change')
        self.assertEqual(plan['phase'], 'recovery_needed')
        self.assertFalse(any(e.startswith('commit:') for e in f.events))

    def test_final_source_mutation_is_freshly_refused_and_source_is_never_restored(self):
        f = fixture(autokey=True)
        plan = prepare(f)
        with self.assertRaisesRegex(RuntimeError, 'recovery needs review'):
            execute(f, plan, after_key=lambda side: f.contents.update(source='external-source-change'))
        self.assertEqual(f.contents['source'], 'external-source-change')
        self.assertEqual(service._raw(f.rig), plan['before_raw'])
        self.assertEqual(f.keys, f.key_before)

    def test_changed_key_policy_on_second_side_refuses_before_either_write(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        f.keying.validate_keying.side_effect = [None, ValueError('second policy changed')]
        with backend(f), patch.object(service, '_solve') as solve:
            with self.assertRaisesRegex(ValueError, 'second policy changed'):
                service.apply_wrist_neutral(f.context, plan)
        solve.assert_not_called()
        self.assertEqual(f.keys, f.key_before)


class AtomicRecovery(unittest.TestCase):
    def test_all_pair_faults_restore_both_domains_before_full_recovery_proof(self):
        phases = ('after_batch_first_rotation', 'after_batch_second_rotation',
                  'after_batch_target_verification', 'after_batch_first_key', 'after_batch_second_key')
        for phase in phases:
            with self.subTest(phase=phase):
                f = fixture(mirror=True, autokey=True)
                plan = prepare(f)
                def fault(actual):
                    if actual == phase:
                        raise RuntimeError('actual failure ' + phase)
                with self.assertRaisesRegex(RuntimeError, 'actual failure'):
                    execute(f, plan, fault=fault)
                self.assertEqual(f.keys, f.key_before)
                self.assertEqual(service._raw(f.rig), plan['before_raw'])
                self.assertEqual(plan['transaction']['phase'], 'rolled_back')
                self.assertEqual(f.proof.validate_variant.call_args.kwargs, {'final': True})
                restores = [i for i, event in enumerate(f.events) if event.startswith('restore:')]
                verifies = [i for i, event in enumerate(f.events) if event.startswith('verify:')]
                if restores and verifies:
                    self.assertLess(max(restores), min(verifies))
                self.assertFalse(any(event.startswith('commit:') for event in f.events))

    def test_second_key_partial_write_restores_both_then_full_original_proof(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        with self.assertRaisesRegex(RuntimeError, 'partial native key insertion'):
            execute(f, plan, key_failure=f.right.name)
        self.assertEqual(f.events, ['restore:target.R', 'restore:target.L', 'verify:target.L', 'verify:target.R'])
        self.assertEqual(f.keys, f.key_before)
        self.assertEqual(plan['phase'], 'rolled_back')

    def test_single_fault_keeps_fresh_pose_capture_and_key_rollback(self):
        f = fixture(autokey=True)
        plan = prepare(f)
        def fault(phase):
            if phase == 'after_key_rotation':
                raise RuntimeError('single real key fault')
        with self.assertRaisesRegex(RuntimeError, 'single real key fault'):
            execute(f, plan, fault=fault)
        self.assertEqual(f.keys, f.key_before)
        self.assertEqual(service._raw(f.rig), plan['before_raw'])
        self.assertEqual(plan['transaction']['phase'], 'rolled_back')

    def test_completed_and_rolled_back_plans_cannot_replay_and_next_operation_is_fresh(self):
        f = fixture(mirror=True, autokey=False)
        plan = prepare(f)
        execute(f, plan)
        with backend(f), patch.object(service, '_solve') as solve:
            with self.assertRaises(ValueError):
                service.apply_wrist_neutral(f.context, plan)
        solve.assert_not_called()
        next_plan = prepare(f)
        self.assertIsNot(next_plan['transaction'], plan['transaction'])
        self.assertEqual(f.proof.prepare_variant.call_count, 2)
        self.assertEqual(f.api.target._capture.call_count, 2)


if __name__ == '__main__':
    unittest.main()
