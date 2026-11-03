"""Scoped writer contracts; all legacy full-content test files stay unchanged.

Native matrices and owner admission have their own independent native tests.
Here the actual prepare/apply/rollback routing uses an observable local owner,
with every former full proof/capture/broad-restore entry set to fail if called.
"""
from contextlib import contextmanager
import importlib
import unittest
from unittest.mock import Mock, patch

import test_limb_ik_wrist_rotation as single
import test_limb_ik_wrist_batch as pair

service = single.wrist
transaction = importlib.import_module(single.PACKAGE + '.limb_ik_wrist_transaction')


def fixture(*, mirror=False, action=True, autokey=False):
    f = pair.fixture(mirror=mirror, action=action, autokey=autokey)
    f.graph = 'owned-graph'
    f.permission = 'CUSTOM'
    f.external = dict(Source='source-content', other_curve='unrelated-curve', other_raw=(.1, .2, .3))
    f.api.match._update = Mock()
    f.keys['foreign'] = 'foreign-key'
    f.key_before = dict(f.keys)

    def forbidden(*args, **kwargs):
        raise AssertionError('A full content/capture/broad-restore path was called.')
    f.offset.prepare_variant.side_effect = forbidden
    f.offset._variant_owner.side_effect = forbidden
    f.offset._unaffected.side_effect = forbidden
    f.api.target._capture.side_effect = forbidden
    f.api.target._validate_capture.side_effect = forbidden
    f.api.revision._restore_current.side_effect = forbidden
    f.api.revision.validate.side_effect = forbidden
    f.api.pose._admission.side_effect = forbidden
    f.api.pose._structure.side_effect = forbidden
    f.physics.status.side_effect = forbidden
    f.api.limb._validate_inventory.side_effect = forbidden
    f.api.limb.LIMB_SPEC = {'ARM': {'target': 'target.{side}'}}
    def local_inventory(limb, match, rig, target):
        found = [(key, entry) for key, entry in f.inventory['rigs'].items() if entry['target'] is target.bone]
        if len(found) != 1:
            raise ValueError('local actual wrist graph is missing')
        return found[0]
    f.local_inventory = Mock(side_effect=local_inventory)

    def prepare(context, rig):
        if f.permission != 'CUSTOM':
            raise ValueError('current Source/Original owner is not editable')
        ad = rig.animation_data
        return dict(context=context, rig=rig, data=rig.data, action=ad.action,
            slot=ad.action_slot.handle if ad.action else None, permission=f.permission)

    def validate(context, owner):
        ad = f.rig.animation_data
        if context is not owner['context'] or f.permission != owner['permission']:
            raise ValueError('current owner permission changed')
        if ad.action is not owner['action'] or (ad.action and ad.action_slot.handle != owner['slot']):
            raise ValueError('current Action/slot owner changed')
        return owner

    f.guard = single.NS(prepare=Mock(side_effect=prepare), validate=Mock(side_effect=validate),
        resolve=Mock(side_effect=lambda context, rig, target, owner: service._resolve_local(context, rig, target, owner)))
    return f


@contextmanager
def backend(f):
    with single.backend(f), patch.object(service, '_owner_guard', return_value=f.guard), \
         patch.object(service, '_local_inventory', side_effect=f.local_inventory), \
         patch.object(service, '_graph_stamp', side_effect=lambda rig, target, end, forearm, key, entry: (f.graph, key)), \
         patch.object(service, '_keying', return_value=f.keying):
        yield


def prepare(f, **kwargs):
    with backend(f), patch.object(service, '_matrix_finite', return_value=True), \
         patch.object(service, '_reference', side_effect=lambda api, rig, end: {end.name: tuple(end.rotation_euler)}):
        return service.prepare_wrist_neutral(f.context, **kwargs)


def execute(f, plan, *, fault=None, key_failure=None, after_key=None, neutral_left=False, neutral_all=False):
    def solve(context, side):
        if neutral_all or neutral_left and side['target'] is f.target:
            return 0
        side['written_rotation'] = True
        setattr(side['target'], side['rotation_path'], (.2, -.3, .4))
        service._fault('after_rotation_write')
        return 1

    def verify(context, side):
        service._unchanged_inputs(side, allow_target_rotation=True)
        return dict(matrix=0.)

    def key(context, side):
        if not side['autokey']:
            return False
        name = side['target'].name
        def restore():
            f.events.append('restore:' + name)
            f.keys[name] = f.key_before[name]
        def check():
            f.events.append('verify:' + name)
            if f.keys[name] != f.key_before[name]:
                raise ValueError('owned local keys did not restore')
            # The real key service closes all callbacks after verification.
            # Native settling must use a durable rollback-entry marker.
            for field in ('key_restore', 'key_verify', 'key_rollback', 'key_commit'):
                side[field] = None
        side['key_restore'], side['key_verify'] = restore, check
        side['key_rollback'] = lambda: (restore(), check())
        side['key_commit'] = lambda: f.events.append('commit:' + name)
        f.keys[name] = 'new:' + name
        if after_key:
            after_key(side)
        if key_failure == name:
            raise RuntimeError('actual partial local key write')
        return True

    with backend(f), patch.object(service, '_solve', side_effect=solve), \
         patch.object(service, '_verify', side_effect=verify), \
         patch.object(service, '_reference', side_effect=lambda api, rig, end: {end.name: tuple(end.rotation_euler)}), \
         patch.object(service, '_key_rotation', side_effect=key), patch.object(service, '_fault', side_effect=fault):
        return service.apply_wrist_neutral(f.context, plan)


class LocalPreparation(unittest.TestCase):
    def test_one_owner_and_only_one_or_two_target_snapshots(self):
        for mirror in (False, True):
            with self.subTest(mirror=mirror):
                f = fixture(mirror=mirror)
                plan = prepare(f)
                expected = {f.target.name, f.right.name} if mirror else {f.target.name}
                self.assertEqual(set(plan['before_raw']), expected)
                self.assertNotIn('capture', plan)
                self.assertNotIn('structure', plan)
                self.assertEqual(f.guard.prepare.call_count, 1)
                self.assertEqual(f.local_inventory.call_count, 2 if mirror else 1)
                f.api.limb._validate_inventory.assert_not_called()

    def test_no_action_off_succeeds_without_any_capture_or_content_proof(self):
        f = fixture(mirror=True, action=False)
        plan = prepare(f)
        result = execute(f, plan)
        self.assertFalse(result['keyed'])
        f.api.target._capture.assert_not_called()
        f.api.revision._restore_current.assert_not_called()

    def test_no_action_autokey_refuses_before_any_target_write(self):
        f = fixture(action=False, autokey=True)
        before = service._raw_one(f.target)
        with self.assertRaisesRegex(ValueError, 'editable Custom'):
            prepare(f)
        self.assertEqual(service._raw_one(f.target), before)

    def test_an_unrelated_bone_raw_is_never_read(self):
        f = fixture(mirror=True)
        class Unrelated:
            name = 'Unrelated author bone'
            def __getattr__(self, key):
                raise AssertionError('Unwritten bone was inspected: ' + key)
        f.rig.pose.bones[Unrelated.name] = Unrelated()
        plan = prepare(f)
        execute(f, plan)

    def test_global_physics_status_is_not_admission_or_recovery(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        execute(f, plan)
        f.physics.status.assert_not_called()

    def test_active_keying_set_is_irrelevant_with_autokey_off(self):
        f = fixture(mirror=True)
        f.context.scene.keying_sets.active = object()
        f.context.scene.keying_sets_all.active = object()
        plan = prepare(f)
        self.assertFalse(execute(f, plan)['keyed'])

    def test_active_keying_set_refuses_before_writing_with_autokey_on(self):
        f = fixture(autokey=True)
        f.context.scene.keying_sets.active = object()
        before = service._raw_one(f.target)
        with self.assertRaisesRegex(ValueError, 'keying policy'):
            prepare(f)
        self.assertEqual(service._raw_one(f.target), before)


class ScopedStaleness(unittest.TestCase):
    def test_target_owner_frame_mode_policy_and_owned_graph_changes_refuse_before_writing(self):
        mutations = (
            lambda f: setattr(f.target, 'rotation_euler', (.3, .2, .1)),
            lambda f: setattr(f.target, 'rotation_mode', 'ZYX'),
            lambda f: setattr(f.context.scene, 'frame_subframe', .5),
            lambda f: setattr(f.rig.pose, 'use_mirror_x', False),
            lambda f: setattr(f.context.scene.tool_settings, 'use_keyframe_insert_auto', False),
            lambda f: setattr(f, 'permission', 'SOURCE'),
            lambda f: setattr(f, 'graph', 'changed-owner-graph'),
            lambda f: setattr(f.rig.animation_data, 'action', object()),
            lambda f: setattr(f.rig.animation_data.action_slot, 'handle', 8),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                f = fixture(mirror=True, autokey=True)
                plan = prepare(f)
                mutate(f)
                before = service._raw_one(f.target)
                with backend(f), patch.object(service, '_solve') as solve:
                    with self.assertRaises(ValueError):
                        service.apply_wrist_neutral(f.context, plan)
                solve.assert_not_called()
                self.assertEqual(service._raw_one(f.target), before)
                f.api.revision._restore_current.assert_not_called()

    def test_local_key_policy_on_second_side_is_checked_before_either_write(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        f.keying.validate_keying.side_effect = [None, ValueError('local policy changed')]
        with backend(f), patch.object(service, '_reference', return_value={'local': 'reference'}), \
             patch.object(service, '_solve') as solve:
            with self.assertRaisesRegex(ValueError, 'local policy changed'):
                service.apply_wrist_neutral(f.context, plan)
        solve.assert_not_called()

    def test_source_other_curve_and_other_raw_edits_after_prepare_are_allowed_and_preserved(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        f.external.update(Source='new Source data', other_curve='new untouched curve', other_raw=(9., 8., 7.))
        f.keys['foreign'] = 'new unrelated key'
        f.hand.rotation_euler = (.4, -.1, .6)
        result = execute(f, plan)
        self.assertTrue(result['keyed'])
        self.assertEqual(f.external['Source'], 'new Source data')
        self.assertEqual(f.keys['foreign'], 'new unrelated key')
        self.assertEqual(f.hand.rotation_euler, (.4, -.1, .6))
        self.assertEqual(plan['sides'][0]['wanted_native'][f.hand.name], f.hand.rotation_euler)

    def test_only_each_requested_graph_is_validated_once_per_phase(self):
        f = fixture(mirror=True)
        plan = prepare(f)
        execute(f, plan)
        self.assertEqual(f.local_inventory.call_count, 6)
        f.api.limb._validate_inventory.assert_not_called()
        self.assertEqual(f.guard.prepare.call_count, 1)
        self.assertEqual(f.guard.validate.call_count, 2)


class LocalRecovery(unittest.TestCase):
    def test_all_five_pair_faults_restore_local_keys_and_written_rotations(self):
        phases = ('after_batch_first_rotation', 'after_batch_second_rotation',
                  'after_batch_target_verification', 'after_batch_first_key', 'after_batch_second_key')
        for phase in phases:
            with self.subTest(phase=phase):
                f = fixture(mirror=True, autokey=True)
                plan = prepare(f)
                def fault(actual):
                    if actual == phase:
                        f.external.update(Source='concurrent Source edit', other_curve='concurrent untouched curve')
                        f.keys['foreign'] = 'concurrent unrelated key'
                        raise RuntimeError('injected local fault')
                with self.assertRaisesRegex(RuntimeError, 'injected local fault'):
                    execute(f, plan, fault=fault)
                self.assertEqual(service._raw_one(f.target), plan['before_raw'][f.target.name])
                self.assertEqual(service._raw_one(f.right), plan['before_raw'][f.right.name])
                self.assertEqual(f.keys[f.target.name], f.key_before[f.target.name])
                self.assertEqual(f.keys[f.right.name], f.key_before[f.right.name])
                self.assertEqual(f.external['Source'], 'concurrent Source edit')
                self.assertEqual(f.keys['foreign'], 'concurrent unrelated key')
                self.assertEqual(plan['phase'], 'rolled_back')
                f.api.revision._restore_current.assert_not_called()

    def test_real_partial_second_key_failure_restores_both_local_domains(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        with self.assertRaisesRegex(RuntimeError, 'actual partial local key write'):
            execute(f, plan, key_failure=f.right.name)
        self.assertEqual(f.events, ['restore:target.R', 'restore:target.L', 'verify:target.L', 'verify:target.R'])
        self.assertEqual(f.keys, f.key_before)

    def test_key_recovery_settles_after_callbacks_close_even_without_rotation_writes(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        assignments = []
        settled = []
        def evaluate_restored_keys(context, rig):
            if not settled:
                # Native key restoration can evaluate the old curve values,
                # replacing the pending neutral input despite zero solver writes.
                for side in plan['sides']:
                    object.__setattr__(side['target'], side['rotation_path'], (.7, -.8, .9))
                settled.append(True)
        f.api.match._update.side_effect = evaluate_restored_keys
        def record(pb, name, value):
            if name == 'rotation_euler':
                assignments.append(pb.name)
            object.__setattr__(pb, name, value)
        with patch.object(single.Bone, '__setattr__', record):
            with self.assertRaisesRegex(RuntimeError, 'actual partial local key write'):
                execute(f, plan, neutral_all=True, key_failure=f.right.name)
        self.assertEqual(assignments, [f.right.name, f.target.name])
        self.assertEqual(f.keys, f.key_before)
        self.assertEqual(service._raw_one(f.target), plan['before_raw'][f.target.name])
        self.assertEqual(service._raw_one(f.right), plan['before_raw'][f.right.name])
        self.assertTrue(all(side['key_restore'] is None and side['key_verify'] is None for side in plan['sides']))
        self.assertEqual(f.api.match._update.call_count, 2)
        self.assertNotIn('had_key_recovery', plan['transaction'])
        self.assertTrue(all(not side['had_key_recovery'] for side in plan['sides']))

    def test_neutral_first_key_fault_reasserts_only_the_armed_side(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        assignments = []
        def record(pb, name, value):
            if name == 'rotation_euler':
                assignments.append(pb.name)
            object.__setattr__(pb, name, value)
        def update(context, rig):
            if f.api.match._update.call_count == 1:
                object.__setattr__(f.target, 'rotation_euler', (.7, -.8, .9))
        f.api.match._update.side_effect = update
        def fault(phase):
            if phase == 'after_batch_first_key':
                f.external['other_raw'] = (7., 8., 9.)
                raise RuntimeError('first neutral key fault')
        with patch.object(single.Bone, '__setattr__', record):
            with self.assertRaisesRegex(RuntimeError, 'first neutral key fault'):
                execute(f, plan, neutral_all=True, fault=fault)
        self.assertEqual(assignments, [f.target.name])
        self.assertEqual(f.keys, f.key_before)
        self.assertEqual(f.external['other_raw'], (7., 8., 9.))
        self.assertEqual(plan['phase'], 'rolled_back')

    def test_single_neutral_key_fault_reasserts_its_active_rotation(self):
        f = fixture(autokey=True)
        plan = prepare(f)
        def update(context, rig):
            if f.api.match._update.call_count == 1:
                object.__setattr__(f.target, 'rotation_euler', (.7, -.8, .9))
        f.api.match._update.side_effect = update
        with self.assertRaisesRegex(RuntimeError, 'actual partial local key write'):
            execute(f, plan, neutral_all=True, key_failure=f.target.name)
        self.assertEqual(service._raw_one(f.target), plan['before_raw'][f.target.name])
        self.assertEqual(f.keys, f.key_before)
        self.assertFalse(plan['had_key_recovery'])
        self.assertEqual(plan['phase'], 'rolled_back')

    def test_changed_rotation_and_keys_settle_before_local_rotation_restore(self):
        f = fixture(mirror=True, autokey=True)
        plan = prepare(f)
        with self.assertRaisesRegex(RuntimeError, 'actual partial local key write'):
            execute(f, plan, key_failure=f.right.name)
        self.assertEqual(f.api.match._update.call_count, 2)
        self.assertEqual(f.keys, f.key_before)
        self.assertTrue(all(side['key_restore'] is None for side in plan['sides']))

    def test_no_op_side_is_not_written_by_rotation_recovery(self):
        f = fixture(mirror=True)
        plan = prepare(f)
        assignments = []
        original = object.__setattr__
        def record(pb, name, value):
            if name == 'rotation_euler':
                assignments.append(pb.name)
            original(pb, name, value)
        def fault(phase):
            if phase == 'after_batch_second_rotation':
                raise RuntimeError('written right fault')
        with patch.object(single.Bone, '__setattr__', record):
            with self.assertRaisesRegex(RuntimeError, 'written right fault'):
                execute(f, plan, fault=fault, neutral_left=True)
        self.assertEqual(assignments, [f.right.name, f.right.name])

    def test_key_recovery_error_still_restores_only_written_target_rotations(self):
        f = fixture(autokey=True)
        plan = prepare(f)
        def fault(phase):
            if phase == 'after_key_rotation':
                plan['key_rollback'] = Mock(side_effect=RuntimeError('owned curve recovery error'))
                raise RuntimeError('fault')
        with self.assertRaisesRegex(RuntimeError, 'recovery needs review'):
            execute(f, plan, fault=fault)
        self.assertEqual(service._raw_one(f.target), plan['before_raw'][f.target.name])
        self.assertEqual(plan['phase'], 'recovery_needed')

    def test_completed_plan_is_not_replayed_or_reused_by_next_operation(self):
        f = fixture(mirror=True)
        plan = prepare(f)
        execute(f, plan)
        with backend(f), patch.object(service, '_solve') as solve:
            with self.assertRaises(ValueError):
                service.apply_wrist_neutral(f.context, plan)
        solve.assert_not_called()
        next_plan = prepare(f)
        self.assertIsNot(next_plan['transaction'], plan['transaction'])
        self.assertIsNot(next_plan['transaction']['owner'], plan['transaction']['owner'])


if __name__ == '__main__':
    unittest.main()
